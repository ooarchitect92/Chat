from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from northstar_api.workers.event_utils import EventEnvelope
from northstar_api.workers.object_cleaner import cleanup_object_key


def _deletion_event(*, tenant_id: UUID, object_key: object) -> EventEnvelope:
    return EventEnvelope(
        event_id=uuid4(),
        event_type="knowledge.source.deleted.v1",
        tenant_id=tenant_id,
        occurred_at=datetime.now(UTC),
        payload={"sourceId": str(uuid4()), "objectKey": object_key},
    )


def test_cleanup_object_key_accepts_only_its_tenant_prefix() -> None:
    tenant_id = uuid4()
    key = f"knowledge/{tenant_id}/2026/09/source.txt"

    assert cleanup_object_key(_deletion_event(tenant_id=tenant_id, object_key=key)) == key


def test_cleanup_object_key_rejects_a_different_tenant_prefix() -> None:
    event_tenant_id = uuid4()
    foreign_key = f"knowledge/{uuid4()}/2026/09/source.txt"

    with pytest.raises(ValueError, match="outside its tenant prefix"):
        cleanup_object_key(_deletion_event(tenant_id=event_tenant_id, object_key=foreign_key))


def test_cleanup_object_key_accepts_null_for_non_file_sources() -> None:
    assert cleanup_object_key(_deletion_event(tenant_id=uuid4(), object_key=None)) is None


def test_cleanup_object_key_rejects_missing_object_key() -> None:
    event = _deletion_event(tenant_id=uuid4(), object_key=None)
    del event.payload["objectKey"]

    with pytest.raises(ValueError, match="missing a valid sourceId or objectKey"):
        cleanup_object_key(event)
