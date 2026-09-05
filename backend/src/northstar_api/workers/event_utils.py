from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import orjson
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from northstar_api.logging import redact
from northstar_api.models import EventQuarantine


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_id: UUID
    event_type: str
    tenant_id: UUID
    occurred_at: datetime
    payload: dict[str, Any]


def parse_envelope(raw: bytes) -> EventEnvelope:
    try:
        value = orjson.loads(raw)
        if not isinstance(value, dict) or value.get("schemaVersion") != 1:
            raise ValueError("unsupported event envelope")
        event_type = value["eventType"]
        payload = value["payload"]
        if not isinstance(event_type, str) or not event_type or len(event_type) > 160:
            raise ValueError("eventType is invalid")
        if not isinstance(payload, dict):
            raise ValueError("event payload is invalid")
        occurred_at = datetime.fromisoformat(str(value["occurredAt"]).replace("Z", "+00:00"))
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        return EventEnvelope(
            event_id=UUID(str(value["eventId"])),
            event_type=event_type,
            tenant_id=UUID(str(value["tenantId"])),
            occurred_at=occurred_at,
            payload={str(key): item for key, item in payload.items()},
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("event envelope validation failed") from exc


async def quarantine_event(
    session: AsyncSession,
    *,
    consumer: str,
    topic: str,
    partition: int,
    offset: int,
    raw: bytes,
    error: BaseException,
) -> None:
    excerpt = raw.decode("utf-8", errors="replace")[:4_000]
    safe_excerpt = redact(excerpt)
    await session.execute(
        pg_insert(EventQuarantine)
        .values(
            consumer=consumer,
            topic=topic[:200],
            partition=partition,
            offset=offset,
            payload_sha256=hashlib.sha256(raw).hexdigest(),
            payload_excerpt=str(safe_excerpt),
            error=f"{type(error).__name__}: {str(error)}"[:500],
        )
        .on_conflict_do_nothing(
            index_elements=[
                EventQuarantine.consumer,
                EventQuarantine.topic,
                EventQuarantine.partition,
                EventQuarantine.offset,
            ]
        )
    )
    await session.commit()
