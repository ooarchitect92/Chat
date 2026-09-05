from __future__ import annotations

import asyncio
import signal
from typing import Any
from uuid import UUID

import structlog
from aiokafka import AIOKafkaConsumer  # type: ignore[import-untyped]
from aiokafka.structs import TopicPartition  # type: ignore[import-untyped]
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from northstar_api.config import get_settings
from northstar_api.database import build_engine
from northstar_api.logging import configure_logging
from northstar_api.models import AgentHealthDaily, ProcessedEvent
from northstar_api.workers.event_utils import EventEnvelope, parse_envelope, quarantine_event

settings = get_settings()
configure_logging(settings)
logger = structlog.get_logger(__name__)
CONSUMER_NAME = "analytics-projector-v1"
TOPICS = [
    "chat.response.completed.v1",
    "conversation.state.changed.v1",
    "feedback.recorded.v1",
]


def _agent_id(event: EventEnvelope) -> UUID:
    try:
        return UUID(str(event.payload["agentId"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("event payload has no valid agentId") from exc


def _analytics_deltas(event: EventEnvelope) -> dict[str, int]:
    deltas = {
        "conversations": 0,
        "responses": 0,
        "resolved": 0,
        "escalated": 0,
        "response_ms_total": 0,
        "positive_feedback": 0,
        "negative_feedback": 0,
    }
    if event.event_type == "chat.response.completed.v1":
        latency = event.payload.get("latencyMs", 0)
        if not isinstance(latency, int) or isinstance(latency, bool):
            raise ValueError("chat event latencyMs is invalid")
        deltas["conversations"] = int(event.payload.get("conversationStarted") is True)
        deltas["responses"] = 1
        deltas["response_ms_total"] = min(max(0, latency), 3_600_000)
    elif event.event_type == "conversation.state.changed.v1":
        previous = event.payload.get("previousState")
        current = event.payload.get("state")
        valid_states = {"open", "resolved", "escalated"}
        if (
            not isinstance(previous, str)
            or not isinstance(current, str)
            or previous not in valid_states
            or current not in valid_states
        ):
            raise ValueError("conversation state transition is invalid")
        deltas["resolved"] = int(current == "resolved") - int(previous == "resolved")
        deltas["escalated"] = int(current == "escalated") - int(previous == "escalated")
    elif event.event_type == "feedback.recorded.v1":
        previous = event.payload.get("previousValue")
        current = event.payload.get("value")
        previous_is_valid = previous is None or (
            isinstance(previous, int) and not isinstance(previous, bool) and previous in (-1, 1)
        )
        current_is_valid = isinstance(current, int) and not isinstance(current, bool) and current in (-1, 1)
        if not previous_is_valid or not current_is_valid:
            raise ValueError("feedback transition is invalid")
        deltas["positive_feedback"] = int(current == 1) - int(previous == 1)
        deltas["negative_feedback"] = int(current == -1) - int(previous == -1)
    else:
        raise ValueError("event type is not supported by this consumer")
    return deltas


async def _project_event(session: AsyncSession, event: EventEnvelope) -> None:
    agent_id = _agent_id(event)
    deltas = _analytics_deltas(event)
    inserted = await session.scalar(
        pg_insert(ProcessedEvent)
        .values(event_id=event.event_id, consumer=CONSUMER_NAME)
        .on_conflict_do_nothing(index_elements=[ProcessedEvent.event_id, ProcessedEvent.consumer])
        .returning(ProcessedEvent.event_id)
    )
    if inserted is None:
        await session.commit()
        return

    nonnegative = {"resolved", "escalated", "positive_feedback", "negative_feedback"}
    updates: dict[str, Any] = {}
    for field, delta in deltas.items():
        current = getattr(AgentHealthDaily, field)
        updates[field] = func.greatest(current + delta, 0) if field in nonnegative else current + delta
    await session.execute(
        pg_insert(AgentHealthDaily)
        .values(
            tenant_id=event.tenant_id,
            agent_id=agent_id,
            day=event.occurred_at.date(),
            **deltas,
        )
        .on_conflict_do_update(
            index_elements=[
                AgentHealthDaily.tenant_id,
                AgentHealthDaily.agent_id,
                AgentHealthDaily.day,
            ],
            set_=updates,
        )
    )
    await session.commit()


async def consume_forever() -> None:
    engine = build_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    consumer = AIOKafkaConsumer(
        *TOPICS,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=CONSUMER_NAME,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        client_id="northstar-analytics",
    )
    await consumer.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop.set)
        except NotImplementedError:
            pass
    logger.info("analytics_consumer_started")
    try:
        while not stop.is_set():
            batch = await consumer.getmany(timeout_ms=1000, max_records=1)
            for messages in batch.values():
                for message in messages:
                    topic_partition = TopicPartition(message.topic, message.partition)
                    raw = message.value if isinstance(message.value, bytes) else b""
                    try:
                        event = parse_envelope(raw)
                        if event.event_type != message.topic:
                            raise ValueError("event type does not match its Kafka topic")
                        _agent_id(event)
                        _analytics_deltas(event)
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
                        logger.error(
                            "analytics_event_quarantined",
                            topic=message.topic,
                            partition=message.partition,
                            offset=message.offset,
                        )
                        await consumer.commit({topic_partition: message.offset + 1})
                        continue
                    try:
                        async with factory() as session:
                            await _project_event(session, event)
                        await consumer.commit({topic_partition: message.offset + 1})
                    except Exception as exc:
                        logger.warning(
                            "analytics_projection_retry",
                            event_id=str(event.event_id),
                            error=type(exc).__name__,
                        )
                        consumer.seek(topic_partition, message.offset)
                        try:
                            await asyncio.wait_for(stop.wait(), timeout=1.0)
                        except TimeoutError:
                            pass
    finally:
        await consumer.stop()
        await engine.dispose()
        logger.info("analytics_consumer_stopped")


def main() -> None:
    if not settings.kafka_enabled:
        raise SystemExit("KAFKA_ENABLED must be true for the analytics consumer")
    asyncio.run(consume_forever())


if __name__ == "__main__":
    main()
