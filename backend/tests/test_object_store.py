from __future__ import annotations

import hashlib
from typing import Any
from uuid import uuid4

import pytest

from northstar_api.config import get_settings
from northstar_api.services.object_store import InvalidUpload, ObjectStore, ObjectStoreUnavailable


class _Body:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def read(self, _: int) -> bytes:
        return self.data


class _FakeS3:
    def __init__(
        self,
        data: bytes,
        *,
        declared_size: int | None = None,
        etag: str | None = '"object-etag"',
        copied_size: int | None = None,
        copied_version_id: str | None = "copy-version-1",
    ) -> None:
        self.data = data
        self.declared_size = len(data) if declared_size is None else declared_size
        self.etag = etag
        self.copied_size = copied_size
        self.copied_version_id = copied_version_id
        self.copied_to: str | None = None
        self.deleted: list[str] = []
        self.delete_arguments: list[dict[str, Any]] = []
        self.post_arguments: dict[str, Any] = {}
        self.checksum = hashlib.sha256(data).hexdigest()

    def generate_presigned_post(self, **kwargs: Any) -> dict[str, Any]:
        self.post_arguments = kwargs
        return {"url": "https://storage.example/upload", "fields": {"key": kwargs["Key"], **kwargs["Fields"]}}

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        del Bucket
        result: dict[str, Any] = {
            "ContentLength": (
                self.copied_size if Key == self.copied_to and self.copied_size is not None else len(self.data)
            ),
            "ContentType": "text/plain",
            "Metadata": {"declared-size": str(self.declared_size), "sha256": self.checksum},
            "VersionId": "version-1",
        }
        if self.etag is not None:
            result["ETag"] = self.etag
        return result

    def get_object(self, *, Bucket: str, Key: str, **_: Any) -> dict[str, Any]:  # noqa: N803
        del Bucket, Key
        return {"Body": _Body(self.data)}

    def copy_object(self, *, Bucket: str, Key: str, **_: Any) -> dict[str, str]:  # noqa: N803
        del Bucket
        self.copied_to = Key
        return {"VersionId": self.copied_version_id} if self.copied_version_id else {}

    def delete_object(self, *, Bucket: str, Key: str, **kwargs: Any) -> None:  # noqa: N803
        del Bucket
        self.deleted.append(Key)
        self.delete_arguments.append({"Key": Key, **kwargs})


class _FakeVersionPaginator:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.arguments: dict[str, Any] = {}

    def paginate(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.arguments = kwargs
        return self.pages


class _FakeVersionedS3:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.paginator = _FakeVersionPaginator(pages)
        self.deleted_batches: list[list[dict[str, str]]] = []
        self.deleted_unversioned: list[str] = []

    def get_bucket_versioning(self, *, Bucket: str) -> dict[str, str]:  # noqa: N803
        del Bucket
        return {"Status": "Enabled"}

    def get_paginator(self, operation: str) -> _FakeVersionPaginator:
        assert operation == "list_object_versions"
        return self.paginator

    def delete_objects(self, *, Bucket: str, Delete: dict[str, Any]) -> dict[str, Any]:  # noqa: N803
        del Bucket
        self.deleted_batches.append(Delete["Objects"])
        return {}

    def delete_object(self, *, Bucket: str, Key: str) -> None:  # noqa: N803
        del Bucket
        self.deleted_unversioned.append(Key)


async def test_presigned_post_is_staged_and_size_constrained(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid4()
    client = _FakeS3(b"verified upload")
    store = ObjectStore(get_settings())
    monkeypatch.setattr(store, "_client", lambda **_: client)

    key, url, fields, _ = await store.presign_post(
        tenant_id=tenant_id,
        filename="policy.txt",
        content_type="text/plain",
        size_bytes=len(client.data),
        checksum_sha256=client.checksum,
    )

    assert key.startswith(f"staging/{tenant_id}/")
    assert url == "https://storage.example/upload"
    assert fields["x-amz-meta-declared-size"] == str(len(client.data))
    assert ["content-length-range", 1, len(client.data)] in client.post_arguments["Conditions"]

    permanent_key, checksum, size = await store.promote_staged(tenant_id, key)
    assert permanent_key == key.replace("staging/", "knowledge/", 1)
    assert checksum == hashlib.sha256(client.data).hexdigest()
    assert size == len(client.data)
    assert client.copied_to == permanent_key
    assert key in client.deleted


async def test_promotion_rejects_a_size_different_from_signed_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid4()
    client = _FakeS3(b"payload", declared_size=100)
    store = ObjectStore(get_settings())
    monkeypatch.setattr(store, "_client", lambda **_: client)

    with pytest.raises(InvalidUpload, match="size does not match"):
        await store.promote_staged(tenant_id, f"staging/{tenant_id}/2026/09/example.txt")

    assert client.copied_to is None


@pytest.mark.parametrize("etag", [None, "", "unquoted", '"contains\nnewline"'])
async def test_promotion_rejects_a_missing_or_malformed_etag(
    monkeypatch: pytest.MonkeyPatch,
    etag: str | None,
) -> None:
    tenant_id = uuid4()
    client = _FakeS3(b"payload", etag=etag)
    store = ObjectStore(get_settings())
    monkeypatch.setattr(store, "_client", lambda **_: client)

    with pytest.raises(InvalidUpload, match="valid ETag"):
        await store.promote_staged(tenant_id, f"staging/{tenant_id}/2026/09/example.txt")

    assert client.copied_to is None


async def test_promotion_size_mismatch_deletes_the_exact_copied_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid4()
    client = _FakeS3(
        b"payload",
        copied_size=999,
        copied_version_id="copied-version-7",
    )
    store = ObjectStore(get_settings())
    monkeypatch.setattr(store, "_client", lambda **_: client)
    staging_key = f"staging/{tenant_id}/2026/09/example.txt"

    with pytest.raises(ObjectStoreUnavailable, match="did not preserve"):
        await store.promote_staged(tenant_id, staging_key)

    assert client.copied_to is not None
    assert client.delete_arguments == [{"Key": client.copied_to, "VersionId": "copied-version-7"}]


async def test_purge_exact_removes_all_versions_and_markers_across_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid4()
    key = f"knowledge/{tenant_id}/2026/09/policy.txt"
    neighbour = f"{key}.backup"
    client = _FakeVersionedS3(
        [
            {
                "Versions": [
                    {"Key": key, "VersionId": "v3"},
                    {"Key": neighbour, "VersionId": "other-v1"},
                ],
                "DeleteMarkers": [{"Key": key, "VersionId": "marker-2"}],
            },
            {
                "Versions": [{"Key": key, "VersionId": "v1"}],
                "DeleteMarkers": [
                    {"Key": key, "VersionId": "marker-1"},
                    {"Key": neighbour, "VersionId": "other-marker"},
                ],
            },
        ]
    )
    store = ObjectStore(get_settings())
    monkeypatch.setattr(store, "_client", lambda **_: client)

    deleted = await store.purge_exact(key)

    assert deleted == 4
    assert client.paginator.arguments == {"Bucket": get_settings().s3_bucket, "Prefix": key}
    assert client.deleted_batches == [
        [
            {"Key": key, "VersionId": "v3"},
            {"Key": key, "VersionId": "marker-2"},
            {"Key": key, "VersionId": "v1"},
            {"Key": key, "VersionId": "marker-1"},
        ]
    ]
    assert client.deleted_unversioned == []


async def test_purge_exact_uses_plain_delete_for_unversioned_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = f"knowledge/{uuid4()}/2026/09/policy.txt"
    client = _FakeVersionedS3([])
    client.get_bucket_versioning = lambda **_: {}
    store = ObjectStore(get_settings())
    monkeypatch.setattr(store, "_client", lambda **_: client)

    deleted = await store.purge_exact(key)

    assert deleted == 1
    assert client.deleted_unversioned == [key]
    assert client.deleted_batches == []


async def test_purge_exact_respects_s3_multi_delete_batch_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = f"knowledge/{uuid4()}/2026/09/policy.txt"
    versions = [{"Key": key, "VersionId": f"v{index}"} for index in range(1_001)]
    client = _FakeVersionedS3([{"Versions": versions}])
    store = ObjectStore(get_settings())
    monkeypatch.setattr(store, "_client", lambda **_: client)

    deleted = await store.purge_exact(key)

    assert deleted == 1_001
    assert [len(batch) for batch in client.deleted_batches] == [1_000, 1]


async def test_purge_exact_surfaces_partial_version_delete_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = f"knowledge/{uuid4()}/2026/09/policy.txt"
    client = _FakeVersionedS3([{"Versions": [{"Key": key, "VersionId": "v1"}]}])
    client.delete_objects = lambda **_: {"Errors": [{"Key": key, "VersionId": "v1"}]}
    store = ObjectStore(get_settings())
    monkeypatch.setattr(store, "_client", lambda **_: client)

    with pytest.raises(ObjectStoreUnavailable, match="every object version"):
        await store.purge_exact(key)
