from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import PurePath
from typing import Any
from uuid import UUID

import boto3  # type: ignore[import-untyped]
from botocore.client import Config  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from northstar_api.config import Settings, get_settings


class ObjectStoreUnavailable(RuntimeError):
    pass


class InvalidUpload(ValueError):
    pass


ALLOWED_UPLOAD_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/markdown",
    "text/plain",
}


class ObjectStore:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def _client(self, *, public: bool = False) -> Any:
        access_key = self.settings.s3_access_key_id
        secret_key = self.settings.s3_secret_access_key
        if not self.settings.s3_configured or access_key is None or secret_key is None:
            raise ObjectStoreUnavailable("Object storage is not configured")
        return boto3.client(
            "s3",
            endpoint_url=(self.settings.s3_public_endpoint_url if public else self.settings.s3_endpoint_url),
            region_name=self.settings.s3_region,
            aws_access_key_id=access_key.get_secret_value(),
            aws_secret_access_key=secret_key.get_secret_value(),
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def new_key(self, tenant_id: UUID, filename: str, *, prefix: str = "staging") -> str:
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", PurePath(filename).name).strip(".-")[:120]
        safe_name = safe_name or "upload"
        today = datetime.now(UTC)
        return f"{prefix}/{tenant_id}/{today:%Y/%m}/{secrets.token_urlsafe(18)}-{safe_name}"

    async def presign_post(
        self,
        *,
        tenant_id: UUID,
        filename: str,
        content_type: str,
        size_bytes: int,
        checksum_sha256: str,
    ) -> tuple[str, str, dict[str, str], datetime]:
        if content_type not in ALLOWED_UPLOAD_CONTENT_TYPES:
            raise InvalidUpload("Uploaded file type is not supported")
        if size_bytes <= 0 or size_bytes > self.settings.upload_max_bytes:
            raise InvalidUpload("Uploaded file size is not permitted")
        key = self.new_key(tenant_id, filename)
        fields = {
            "Content-Type": content_type,
            "x-amz-meta-declared-size": str(size_bytes),
            "x-amz-meta-sha256": checksum_sha256.lower(),
        }
        conditions: list[Any] = [
            {"Content-Type": content_type},
            {"x-amz-meta-declared-size": str(size_bytes)},
            {"x-amz-meta-sha256": checksum_sha256.lower()},
            ["content-length-range", 1, size_bytes],
        ]

        def generate() -> tuple[str, dict[str, str]]:
            result = self._client(public=True).generate_presigned_post(
                Bucket=self.settings.s3_bucket,
                Key=key,
                Fields=fields,
                Conditions=conditions,
                ExpiresIn=self.settings.s3_presign_ttl_seconds,
            )
            generated_url = result.get("url")
            generated_fields = result.get("fields")
            if not isinstance(generated_url, str) or not isinstance(generated_fields, dict):
                raise ObjectStoreUnavailable("Object storage returned an invalid upload URL")
            return generated_url, {str(name): str(value) for name, value in generated_fields.items()}

        url, signed_fields = await asyncio.to_thread(generate)
        return (
            key,
            url,
            signed_fields,
            datetime.now(UTC) + timedelta(seconds=self.settings.s3_presign_ttl_seconds),
        )

    async def promote_staged(self, tenant_id: UUID, key: str) -> tuple[str, str, int]:
        expected_prefix = f"staging/{tenant_id}/"
        if not key.startswith(expected_prefix):
            raise InvalidUpload("Uploaded object does not belong to this workspace")

        def promote() -> tuple[str, str, int]:
            client = self._client()
            try:
                head = client.head_object(Bucket=self.settings.s3_bucket, Key=key)
                size = int(head.get("ContentLength", 0))
                metadata = {str(name).lower(): str(value) for name, value in head.get("Metadata", {}).items()}
                declared_size = int(metadata.get("declared-size", "0"))
                content_type = str(head.get("ContentType") or "").split(";", 1)[0].strip().lower()
                version_id = str(head.get("VersionId") or "")
                etag = str(head.get("ETag") or "")
            except ClientError as exc:
                error_code = str(exc.response.get("Error", {}).get("Code", ""))
                if error_code in {"404", "NoSuchKey", "NotFound"}:
                    raise InvalidUpload("Uploaded object was not found or has expired") from exc
                raise ObjectStoreUnavailable("Object storage could not validate the upload") from exc
            except (TypeError, ValueError) as exc:
                raise InvalidUpload("Uploaded object metadata is invalid") from exc

            if size <= 0 or size > self.settings.upload_max_bytes or declared_size != size:
                raise InvalidUpload("Uploaded object size does not match the signed request")
            if content_type not in ALLOWED_UPLOAD_CONTENT_TYPES:
                raise InvalidUpload("Uploaded file type is not supported")
            if not re.fullmatch(r'"[^"\r\n]{1,1024}"', etag):
                raise InvalidUpload("Uploaded object is missing a valid ETag")
            expected_checksum = metadata.get("sha256", "").lower()
            if not re.fullmatch(r"[a-f0-9]{64}", expected_checksum):
                raise InvalidUpload("Uploaded object is missing its signed checksum")

            try:
                source_version = (
                    {"VersionId": version_id} if version_id and version_id.lower() != "null" else {}
                )
                response = client.get_object(
                    Bucket=self.settings.s3_bucket,
                    Key=key,
                    **source_version,
                )
                data = response["Body"].read(self.settings.upload_max_bytes + 1)
            except (BotoCoreError, ClientError, KeyError) as exc:
                raise ObjectStoreUnavailable("Object storage could not read the upload") from exc
            if len(data) != size:
                raise InvalidUpload("Uploaded object changed during validation")
            checksum = hashlib.sha256(data).hexdigest()
            if not secrets.compare_digest(expected_checksum, checksum):
                raise InvalidUpload("Uploaded object checksum does not match")

            permanent_key = key.replace("staging/", "knowledge/", 1)
            copied_version_id: str | None = None
            try:
                copy_source = {"Bucket": self.settings.s3_bucket, "Key": key, **source_version}
                copy_result = client.copy_object(
                    Bucket=self.settings.s3_bucket,
                    Key=permanent_key,
                    CopySource=copy_source,
                    CopySourceIfMatch=etag,
                    MetadataDirective="COPY",
                )
                version_value = copy_result.get("VersionId")
                if isinstance(version_value, str) and version_value:
                    copied_version_id = version_value
                copied = client.head_object(Bucket=self.settings.s3_bucket, Key=permanent_key)
            except (BotoCoreError, ClientError, AttributeError) as exc:
                raise ObjectStoreUnavailable("Object storage could not promote the upload") from exc
            try:
                copied_size = int(copied.get("ContentLength", -1))
            except (TypeError, ValueError):
                copied_size = -1
            if copied_size != size:
                try:
                    if copied_version_id is not None:
                        client.delete_object(
                            Bucket=self.settings.s3_bucket,
                            Key=permanent_key,
                            VersionId=copied_version_id,
                        )
                    else:
                        self._purge_exact_sync(client, permanent_key)
                except (BotoCoreError, ClientError, ObjectStoreUnavailable) as exc:
                    raise ObjectStoreUnavailable(
                        "Object storage could not clean up an invalid promoted upload"
                    ) from exc
                raise ObjectStoreUnavailable("Object storage did not preserve the uploaded file")
            client.delete_object(Bucket=self.settings.s3_bucket, Key=key, **source_version)
            return permanent_key, checksum, size

        return await asyncio.to_thread(promote)

    async def delete(self, key: str) -> None:
        def remove() -> None:
            try:
                self._client().delete_object(Bucket=self.settings.s3_bucket, Key=key)
            except (BotoCoreError, ClientError) as exc:
                raise ObjectStoreUnavailable("Object storage could not delete the object") from exc

        await asyncio.to_thread(remove)

    def _purge_exact_sync(self, client: Any, key: str) -> int:
        bucket = self.settings.s3_bucket
        try:
            versioning = client.get_bucket_versioning(Bucket=bucket)
            if versioning.get("Status") not in {"Enabled", "Suspended"}:
                client.delete_object(Bucket=bucket, Key=key)
                return 1

            versioned_objects: list[dict[str, str]] = []
            paginator = client.get_paginator("list_object_versions")
            for page in paginator.paginate(Bucket=bucket, Prefix=key):
                for collection_name in ("Versions", "DeleteMarkers"):
                    for item in page.get(collection_name, []):
                        if item.get("Key") != key:
                            continue
                        version_id = item.get("VersionId")
                        if not isinstance(version_id, str) or not version_id:
                            raise ObjectStoreUnavailable("Object storage returned an invalid object version")
                        versioned_objects.append({"Key": key, "VersionId": version_id})

            for start in range(0, len(versioned_objects), 1_000):
                batch = versioned_objects[start : start + 1_000]
                response = client.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": batch, "Quiet": True},
                )
                errors = response.get("Errors", [])
                if errors:
                    raise ObjectStoreUnavailable("Object storage could not delete every object version")
            return len(versioned_objects)
        except ObjectStoreUnavailable:
            raise
        except (BotoCoreError, ClientError, KeyError, TypeError) as exc:
            raise ObjectStoreUnavailable("Object storage could not purge the object") from exc

    async def purge_exact(self, key: str) -> int:
        """Permanently remove one key, including every version and delete marker."""

        def purge() -> int:
            return self._purge_exact_sync(self._client(), key)

        return await asyncio.to_thread(purge)

    async def download(self, key: str) -> tuple[bytes, dict[str, Any]]:
        def fetch() -> tuple[bytes, dict[str, Any]]:
            client = self._client()
            head = client.head_object(Bucket=self.settings.s3_bucket, Key=key)
            size = int(head.get("ContentLength", 0))
            if size <= 0 or size > self.settings.upload_max_bytes:
                raise ValueError("Uploaded object is empty or exceeds the configured size limit")
            response = client.get_object(Bucket=self.settings.s3_bucket, Key=key)
            data = response["Body"].read(self.settings.upload_max_bytes + 1)
            if len(data) != size or len(data) > self.settings.upload_max_bytes:
                raise ValueError("Uploaded object size changed or exceeds the configured limit")
            return data, {"content_type": head.get("ContentType"), "metadata": head.get("Metadata", {})}

        return await asyncio.to_thread(fetch)


object_store = ObjectStore()
