from __future__ import annotations

import asyncio
import signal
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from northstar_api.config import get_settings
from northstar_api.database import build_engine
from northstar_api.logging import configure_logging
from northstar_api.models import IngestionJob, JobStatus
from northstar_api.workers.tasks import ingest_source

settings = get_settings()
configure_logging(settings)
logger = structlog.get_logger(__name__)


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
