from __future__ import annotations

import asyncio
import signal
from datetime import UTC, datetime

import orjson
import structlog
from aiokafka import AIOKafkaProducer  # type: ignore[import-untyped]
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from northstar_api.config import get_settings
from northstar_api.database import build_engine
from northstar_api.logging import configure_logging
from northstar_api.metrics import OUTBOX_BACKLOG
from northstar_api.models import OutboxEvent

settings = get_settings()
configure_logging(settings)
logger = structlog.get_logger(__name__)


async def publish_forever() -> None:
    engine = build_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        acks="all",
        enable_idempotence=True,
        compression_type="gzip",
        value_serializer=orjson.dumps,
        key_serializer=lambda value: value.encode(),
        client_id="northstar-outbox",
    )
    await producer.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop.set)
        except NotImplementedError:
            pass
    logger.info("outbox_publisher_started")
    try:
        while not stop.is_set():
            published = 0
            async with factory() as session:
                backlog = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(OutboxEvent)
                        .where(OutboxEvent.published_at.is_(None))
                    )
                    or 0
                )
                OUTBOX_BACKLOG.set(backlog)
                rows = (
                    await session.scalars(
                        select(OutboxEvent)
                        .where(OutboxEvent.published_at.is_(None))
                        .order_by(OutboxEvent.occurred_at)
                        .with_for_update(skip_locked=True)
                        .limit(100)
                    )
                ).all()
                for event in rows:
                    envelope = {
                        "eventId": str(event.id),
                        "eventType": event.event_type,
                        "schemaVersion": 1,
                        "tenantId": str(event.tenant_id),
                        "aggregateType": event.aggregate_type,
                        "aggregateId": event.aggregate_id,
                        "occurredAt": event.occurred_at.isoformat(),
                        "payload": event.payload,
                    }
                    try:
                        await producer.send_and_wait(event.event_type, envelope, key=event.aggregate_id)
                        event.published_at = datetime.now(UTC)
                        event.last_error = None
                        published += 1
                    except Exception as exc:
                        event.attempts += 1
                        event.last_error = type(exc).__name__[:500]
                        logger.warning(
                            "outbox_publish_failed", event_id=str(event.id), error=type(exc).__name__
                        )
                        break
                await session.commit()
            if published == 0:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1.0)
                except TimeoutError:
                    pass
    finally:
        await producer.stop()
        await engine.dispose()
        logger.info("outbox_publisher_stopped")


def main() -> None:
    if not settings.kafka_enabled:
        raise SystemExit("KAFKA_ENABLED must be true for the outbox publisher")
    asyncio.run(publish_forever())


if __name__ == "__main__":
    main()
