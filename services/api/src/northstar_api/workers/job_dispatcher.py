from __future__ import annotations

import asyncio
import signal
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from northstar_api.config import get_settings
from northstar_api.database import build_engine
from northstar_api.logging import configure_logging
from northstar_api.models import (
    IngestionJob,
    JobStatus,
    WhatsAppInboundMessage,
    WhatsAppOutboundDelivery,
)
from northstar_api.workers.tasks import (
    ingest_source,
    process_whatsapp_inbound,
    send_whatsapp_human_reply,
)

settings = get_settings()
configure_logging(settings)
logger = structlog.get_logger(__name__)


def whatsapp_dispatch_failure_state(
    current_attempts: int,
    max_attempts: int,
    now: datetime,
) -> tuple[int, str, datetime | None, int]:
    """Return the bounded retry state after a broker publication failure."""

    attempts = current_attempts + 1
    exhausted = attempts >= max_attempts
    delay = min(300, 2 ** min(attempts, 8))
    return (
        attempts,
        "dispatch_failed" if exhausted else "failed",
        None if exhausted else now + timedelta(seconds=delay),
        delay,
    )


async def dispatch_forever() -> None:
    engine = build_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop.set)
        except NotImplementedError:
            pass
    logger.info("job_dispatcher_started")
    try:
        while not stop.is_set():
            dispatched = 0
            now = datetime.now(UTC)
            async with factory() as session:
                await session.execute(
                    update(WhatsAppInboundMessage)
                    .where(
                        WhatsAppInboundMessage.status.in_(("queued", "failed")),
                        WhatsAppInboundMessage.dispatch_attempts >= settings.whatsapp_dispatch_max_attempts,
                        or_(
                            WhatsAppInboundMessage.next_dispatch_at.is_(None),
                            WhatsAppInboundMessage.next_dispatch_at <= now,
                        ),
                    )
                    .values(status="dispatch_failed", next_dispatch_at=None)
                )
                await session.execute(
                    update(WhatsAppOutboundDelivery)
                    .where(
                        WhatsAppOutboundDelivery.status.in_(("queued", "failed")),
                        WhatsAppOutboundDelivery.dispatch_attempts >= settings.whatsapp_dispatch_max_attempts,
                        or_(
                            WhatsAppOutboundDelivery.next_dispatch_at.is_(None),
                            WhatsAppOutboundDelivery.next_dispatch_at <= now,
                        ),
                    )
                    .values(status="dispatch_failed", next_dispatch_at=None)
                )
                jobs = (
                    await session.scalars(
                        select(IngestionJob)
                        .where(
                            IngestionJob.status == JobStatus.QUEUED,
                            IngestionJob.dispatched_at.is_(None),
                            or_(
                                IngestionJob.next_dispatch_at.is_(None),
                                IngestionJob.next_dispatch_at <= now,
                            ),
                        )
                        .order_by(IngestionJob.created_at)
                        .with_for_update(skip_locked=True)
                        .limit(50)
                    )
                ).all()
                for job in jobs:
                    try:
                        await asyncio.to_thread(
                            ingest_source.apply_async,
                            args=[str(job.source_id)],
                            task_id=str(job.id),
                            retry=True,
                        )
                        job.dispatched_at = now
                        job.dispatch_attempts += 1
                        dispatched += 1
                    except Exception as exc:
                        job.dispatch_attempts += 1
                        delay = min(300, 2 ** min(job.dispatch_attempts, 8))
                        job.next_dispatch_at = now + timedelta(seconds=delay)
                        job.error_json = {
                            "type": type(exc).__name__,
                            "message": "Broker publication failed; automatic retry scheduled.",
                        }
                        logger.warning(
                            "job_dispatch_failed",
                            job_id=str(job.id),
                            error=type(exc).__name__,
                            retry_in=delay,
                        )
                inbound_receipts = (
                    await session.scalars(
                        select(WhatsAppInboundMessage)
                        .where(
                            WhatsAppInboundMessage.status.in_(("queued", "failed")),
                            WhatsAppInboundMessage.dispatch_attempts
                            < settings.whatsapp_dispatch_max_attempts,
                            or_(
                                WhatsAppInboundMessage.next_dispatch_at.is_(None),
                                WhatsAppInboundMessage.next_dispatch_at <= now,
                            ),
                        )
                        .order_by(WhatsAppInboundMessage.created_at)
                        .with_for_update(skip_locked=True)
                        .limit(50)
                    )
                ).all()
                for receipt in inbound_receipts:
                    try:
                        await asyncio.to_thread(
                            process_whatsapp_inbound.apply_async,
                            args=[str(receipt.id)],
                            queue="whatsapp.inbound",
                            routing_key="whatsapp.inbound",
                            retry=True,
                        )
                        receipt.status = "queued"
                        receipt.dispatched_at = now
                        receipt.dispatch_attempts += 1
                        receipt.next_dispatch_at = now + timedelta(minutes=5)
                        receipt.last_error = None
                        dispatched += 1
                    except Exception as exc:
                        (
                            receipt.dispatch_attempts,
                            receipt.status,
                            receipt.next_dispatch_at,
                            delay,
                        ) = whatsapp_dispatch_failure_state(
                            receipt.dispatch_attempts,
                            settings.whatsapp_dispatch_max_attempts,
                            now,
                        )
                        receipt.last_error = "queue dispatch unavailable"
                        logger.warning(
                            "whatsapp_inbound_dispatch_failed",
                            receipt_id=str(receipt.id),
                            error=type(exc).__name__,
                            retry_in=delay,
                        )
                outbound_deliveries = (
                    await session.scalars(
                        select(WhatsAppOutboundDelivery)
                        .where(
                            WhatsAppOutboundDelivery.status.in_(("queued", "failed")),
                            WhatsAppOutboundDelivery.dispatch_attempts
                            < settings.whatsapp_dispatch_max_attempts,
                            or_(
                                WhatsAppOutboundDelivery.next_dispatch_at.is_(None),
                                WhatsAppOutboundDelivery.next_dispatch_at <= now,
                            ),
                        )
                        .order_by(WhatsAppOutboundDelivery.created_at)
                        .with_for_update(skip_locked=True)
                        .limit(50)
                    )
                ).all()
                for delivery in outbound_deliveries:
                    try:
                        await asyncio.to_thread(
                            send_whatsapp_human_reply.apply_async,
                            args=[str(delivery.id)],
                            queue="whatsapp.outbound",
                            routing_key="whatsapp.outbound",
                            retry=True,
                        )
                        delivery.status = "queued"
                        delivery.dispatched_at = now
                        delivery.dispatch_attempts += 1
                        delivery.next_dispatch_at = now + timedelta(minutes=5)
                        delivery.last_error = None
                        dispatched += 1
                    except Exception as exc:
                        (
                            delivery.dispatch_attempts,
                            delivery.status,
                            delivery.next_dispatch_at,
                            delay,
                        ) = whatsapp_dispatch_failure_state(
                            delivery.dispatch_attempts,
                            settings.whatsapp_dispatch_max_attempts,
                            now,
                        )
                        delivery.last_error = "queue dispatch unavailable"
                        logger.warning(
                            "whatsapp_outbound_dispatch_failed",
                            delivery_id=str(delivery.id),
                            error=type(exc).__name__,
                            retry_in=delay,
                        )
                await session.commit()
            if dispatched == 0:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1.0)
                except TimeoutError:
                    pass
    finally:
        await engine.dispose()
        logger.info("job_dispatcher_stopped")


def main() -> None:
    if not settings.background_dispatch_enabled:
        raise SystemExit("BACKGROUND_DISPATCH_ENABLED must be true for the job dispatcher")
    asyncio.run(dispatch_forever())


if __name__ == "__main__":
    main()
