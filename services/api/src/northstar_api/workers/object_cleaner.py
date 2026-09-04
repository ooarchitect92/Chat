from __future__ import annotations

import asyncio
import signal
from uuid import UUID

import structlog
from aiokafka import AIOKafkaConsumer  # type: ignore[import-untyped]
from aiokafka.structs import TopicPartition  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from northstar_api.config import get_settings
from northstar_api.database import build_engine
from northstar_api.logging import configure_logging
from northstar_api.models import ProcessedEvent
from northstar_api.services.object_store import ObjectStore, object_store
from northstar_api.workers.event_utils import EventEnvelope, parse_envelope, quarantine_event

settings = get_settings()
configure_logging(settings)
logger = structlog.get_logger(__name__)

TOPIC = "knowledge.source.deleted.v1"
CONSUMER_NAME = "knowledge-object-cleaner-v1"
MAX_OBJECT_KEY_BYTES = 1_024
MAX_RETRY_DELAY_SECONDS = 30.0


def cleanup_object_key(event: EventEnvelope) -> str | None:
    if event.event_type != TOPIC:
        raise ValueError("event type is not supported by this consumer")
    try:
        UUID(str(event.payload["sourceId"]))
        object_key = event.payload["objectKey"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("event payload is missing a valid sourceId or objectKey") from exc

    if object_key is None:
        return None
    if not isinstance(object_key, str):
        raise ValueError("event payload objectKey is invalid")
    expected_prefix = f"knowledge/{event.tenant_id}/"
    if not object_key.startswith(expected_prefix) or len(object_key) == len(expected_prefix):
        raise ValueError("event payload objectKey is outside its tenant prefix")
    if len(object_key.encode("utf-8")) > MAX_OBJECT_KEY_BYTES or any(
        ord(character) < 32 or ord(character) == 127 for character in object_key
    ):
        raise ValueError("event payload objectKey is invalid")
    return object_key


async def _was_processed(
    session: AsyncSession,
    *,
    event_id: UUID,
) -> bool:
    processed = await session.scalar(
        select(ProcessedEvent.event_id).where(
            ProcessedEvent.event_id == event_id,
            ProcessedEvent.consumer == CONSUMER_NAME,
        )
    )
    return processed is not None


async def _mark_processed(session: AsyncSession, *, event_id: UUID) -> None:
    await session.execute(
        pg_insert(ProcessedEvent)
        .values(event_id=event_id, consumer=CONSUMER_NAME)
        .on_conflict_do_nothing(index_elements=[ProcessedEvent.event_id, ProcessedEvent.consumer])
    )
    await session.commit()


async def process_cleanup_event(
    factory: async_sessionmaker[AsyncSession],
    event: EventEnvelope,
    object_key: str | None,
    store: ObjectStore = object_store,
) -> int | None:
    async with factory() as session:
        if await _was_processed(session, event_id=event.event_id):
            return None

    deleted = await store.purge_exact(object_key) if object_key is not None else 0

    # Purging is intentionally completed before this insert. If the process exits
    # between the two operations, Kafka redelivers and the idempotent purge repeats.
    async with factory() as session:
        await _mark_processed(session, event_id=event.event_id)
    return deleted


async def _wait_before_retry(stop: asyncio.Event, delay: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=delay)
    except TimeoutError:
        pass


async def consume_forever() -> None:
    engine = build_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    consumer = AIOKafkaConsumer(
        TOPIC,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=CONSUMER_NAME,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        client_id="northstar-object-cleaner",
    )
    await consumer.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop.set)
        except NotImplementedError:
            pass

    retry_delay = 1.0
    logger.info("object_cleaner_started")
    try:
        while not stop.is_set():
            batch = await consumer.getmany(timeout_ms=1_000, max_records=1)
            for messages in batch.values():
                for message in messages:
                    topic_partition = TopicPartition(message.topic, message.partition)
                    raw = message.value if isinstance(message.value, bytes) else b""
                    try:
                        event = parse_envelope(raw)
                        if event.event_type != message.topic:
                            raise ValueError("event type does not match its Kafka topic")
                        object_key = cleanup_object_key(event)
                    except ValueError as exc:
                        async with factory() as session:
                            await quarantine_event(
                                session,
                                consumer=CONSUMER_NAME,
                                topic=message.topic,
                                partition=message.partition,
                                offset=message.offset,
                                raw=raw,
                                error=exc,
                            )
                        await consumer.commit({topic_partition: message.offset + 1})
                        retry_delay = 1.0
                        logger.error(
                            "object_cleanup_event_quarantined",
                            topic=message.topic,
                            partition=message.partition,
                            offset=message.offset,
                        )
                        continue

                    try:
                        deleted = await process_cleanup_event(factory, event, object_key)
                        await consumer.commit({topic_partition: message.offset + 1})
                        retry_delay = 1.0
                        logger.info(
                            "knowledge_object_cleanup_completed",
                            event_id=str(event.event_id),
                            deleted_versions=deleted,
                            duplicate=deleted is None,
                        )
                    except Exception as exc:
                        logger.warning(
                            "knowledge_object_cleanup_retry",
                            event_id=str(event.event_id),
                            error=type(exc).__name__,
                            retry_in_seconds=retry_delay,
                        )
                        consumer.seek(topic_partition, message.offset)
                        await _wait_before_retry(stop, retry_delay)
                        retry_delay = min(retry_delay * 2, MAX_RETRY_DELAY_SECONDS)
    finally:
        await consumer.stop()
        await engine.dispose()
        logger.info("object_cleaner_stopped")


def main() -> None:
    if not settings.kafka_enabled:
        raise SystemExit("KAFKA_ENABLED must be true for the object cleaner")
    asyncio.run(consume_forever())


if __name__ == "__main__":
    main()
