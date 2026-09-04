from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import structlog
from celery.signals import task_failure  # type: ignore[import-untyped]
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from northstar_api.config import get_settings
from northstar_api.database import build_engine
from northstar_api.metrics import INGESTION_TASKS
from northstar_api.services.ingestion import ingest_source_async
from northstar_api.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="northstar.ingest_source",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=5,
)
def ingest_source(_task: object, source_id: str) -> None:
    async def execute() -> None:
        engine = build_engine(get_settings())
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)

            async def run_ingestion() -> None:
                async with factory() as session:
                    await ingest_source_async(session, UUID(source_id))

            if engine.dialect.name != "postgresql":
                await run_ingestion()
            else:
                # A session-level advisory lock lives on its own connection. The
                # ingestion session must use a separate engine transaction so its
                # commits are not swallowed by the lock connection's outer scope.
                async with engine.connect() as lock_connection:
                    lock_key = f"ingest:{source_id}"
                    acquired = bool(
                        await lock_connection.scalar(
                            text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
                            {"key": lock_key},
                        )
                    )
                    await lock_connection.commit()
                    if not acquired:
                        logger.info("ingestion_already_running", source_id=source_id)
                        INGESTION_TASKS.labels("duplicate").inc()
                        return
                    try:
                        await run_ingestion()
                    finally:
                        await lock_connection.execute(
                            text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                            {"key": lock_key},
                        )
                        await lock_connection.commit()
            INGESTION_TASKS.labels("success").inc()
        finally:
            await engine.dispose()

    asyncio.run(execute())


@task_failure.connect  # type: ignore[untyped-decorator]
def report_final_failure(
    task_id: object | None = None,
    exception: BaseException | None = None,
    sender: object | None = None,
    **_: Any,
) -> None:
    INGESTION_TASKS.labels("failure").inc()
    logger.error(
        "celery_task_failed",
        task_id=str(task_id),
        task=getattr(sender, "name", "unknown"),
        error=type(exception).__name__ if exception else "unknown",
    )
