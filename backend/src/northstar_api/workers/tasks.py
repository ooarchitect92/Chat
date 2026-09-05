from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from celery.signals import task_failure  # type: ignore[import-untyped]
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from northstar_api.config import get_settings
from northstar_api.database import build_engine
from northstar_api.metrics import INGESTION_TASKS
from northstar_api.models import (
    Channel,
    Conversation,
    ConversationState,
    Message,
    WhatsAppConnection,
    WhatsAppInboundMessage,
    WhatsAppOutboundDelivery,
)
from northstar_api.schemas import ChatStreamRequest
from northstar_api.services.chat import chat_coordinator
from northstar_api.services.ingestion import ingest_source_async
from northstar_api.services.whatsapp import (
    WhatsAppGraphError,
    split_whatsapp_text,
    token_cipher,
    token_expiry_is_current,
    whatsapp_client,
)
from northstar_api.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)

INBOUND_TERMINAL_STATUSES = frozenset(
    {"processed", "ignored", "cancelled", "rejected", "reconnect_required", "dispatch_failed"}
)
OUTBOUND_TERMINAL_STATUSES = frozenset(
    {"sent", "cancelled", "rejected", "reconnect_required", "dispatch_failed"}
)


def whatsapp_thread_lock_key(connection_id: UUID, recipient_wa_id: str) -> str:
    """Serialize AI and human sends for one WhatsApp customer thread."""

    return f"whatsapp-thread:{connection_id}:{recipient_wa_id}"


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


async def _process_whatsapp_inbound_async(event_id: UUID) -> None:
    engine = build_engine(get_settings())
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def run() -> None:
        async with factory() as session:
            receipt = await session.scalar(
                select(WhatsAppInboundMessage).where(WhatsAppInboundMessage.id == event_id).with_for_update()
            )
            if not receipt or receipt.status in INBOUND_TERMINAL_STATUSES:
                await session.rollback()
                return
            if receipt.connection_id is None:
                receipt.status = "cancelled"
                receipt.last_error = "WhatsApp connection was disconnected"
                await session.commit()
                return
            receipt.status = "processing"
            receipt.last_error = None
            await session.commit()

            connection = await session.scalar(
                select(WhatsAppConnection).where(
                    WhatsAppConnection.id == receipt.connection_id,
                    WhatsAppConnection.tenant_id == receipt.tenant_id,
                    WhatsAppConnection.status == "connected",
                )
            )
            if not connection:
                receipt.status = "ignored"
                receipt.last_error = "connection removed"
                await session.commit()
                return
            if not token_expiry_is_current(connection.token_expires_at):
                connection.status = "reconnect_required"
                receipt.status = "reconnect_required"
                receipt.last_error = "WhatsApp access token expired"
                await session.commit()
                return
            customer_message_at = receipt.provider_timestamp or receipt.created_at
            if customer_message_at.tzinfo is None:
                customer_message_at = customer_message_at.replace(tzinfo=UTC)
            now = datetime.now(UTC)
            if customer_message_at < now - timedelta(hours=24) or customer_message_at > now + timedelta(
                minutes=5
            ):
                receipt.status = "rejected"
                receipt.last_error = "24-hour customer-service window is closed"
                await session.commit()
                return
            conversation = await session.scalar(
                select(Conversation)
                .where(
                    Conversation.tenant_id == receipt.tenant_id,
                    Conversation.agent_id == connection.agent_id,
                    Conversation.channel == Channel.WHATSAPP,
                    Conversation.visitor_id == receipt.sender_wa_id,
                    Conversation.state != ConversationState.RESOLVED,
                )
                .order_by(Conversation.updated_at.desc())
                .limit(1)
            )
            prepared = await chat_coordinator.prepare(
                session,
                tenant_id=receipt.tenant_id,
                request=ChatStreamRequest(
                    agent_id=connection.agent_id,
                    message=receipt.message_text,
                    conversation_id=conversation.id if conversation else None,
                    visitor_id=receipt.sender_wa_id,
                    idempotency_key=(
                        "wa:" + hashlib.sha256(receipt.provider_message_id.encode()).hexdigest()
                    ),
                ),
                channel=Channel.WHATSAPP,
            )
            conversation = await session.get(Conversation, prepared.conversation_id)
            if conversation and receipt.conversation_id is None:
                if receipt.sender_name:
                    conversation.visitor_name = receipt.sender_name
                conversation.unread_count += 1
                receipt.conversation_id = prepared.conversation_id
                await session.commit()

            access_token = token_cipher.decrypt(connection.access_token_encrypted)
            chunks = split_whatsapp_text(prepared.answer)
            sent_ids = list(receipt.outbound_message_ids or [])
            for chunk in chunks[len(sent_ids) :]:
                provider_id = await whatsapp_client.send_text(
                    access_token,
                    phone_number_id=connection.phone_number_id,
                    to=receipt.sender_wa_id,
                    text=chunk,
                )
                sent_ids.append(provider_id)
                receipt.outbound_message_ids = list(sent_ids)
                receipt.outbound_message_id = provider_id
                receipt.conversation_id = prepared.conversation_id
                await session.commit()
            receipt.status = "processed"
            receipt.processed_at = datetime.now(UTC)
            receipt.conversation_id = prepared.conversation_id
            receipt.last_error = None
            connection.last_error = None
            await session.commit()

    try:
        if engine.dialect.name != "postgresql":
            await run()
        else:
            async with factory() as lookup_session:
                lock_receipt = await lookup_session.get(WhatsAppInboundMessage, event_id)
                if (
                    not lock_receipt
                    or lock_receipt.status in INBOUND_TERMINAL_STATUSES
                    or lock_receipt.connection_id is None
                ):
                    return
                lock_key = whatsapp_thread_lock_key(lock_receipt.connection_id, lock_receipt.sender_wa_id)
            async with engine.connect() as lock_connection:
                async with lock_connection.begin():
                    await lock_connection.execute(
                        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                        {"key": lock_key},
                    )
                    await run()
    except Exception as exc:
        async with factory() as session:
            receipt = await session.get(WhatsAppInboundMessage, event_id)
            if receipt and receipt.status not in INBOUND_TERMINAL_STATUSES:
                permanent_graph_error = isinstance(exc, WhatsAppGraphError) and not exc.retryable
                receipt.status = (
                    "reconnect_required"
                    if isinstance(exc, WhatsAppGraphError) and exc.requires_reconnect
                    else "rejected"
                    if permanent_graph_error
                    else "failed"
                )
                receipt.last_error = f"processing failed: {type(exc).__name__}"[:500]
                connection = (
                    await session.get(WhatsAppConnection, receipt.connection_id)
                    if receipt.connection_id is not None
                    else None
                )
                if connection:
                    connection.last_error = "Inbound message processing failed"
                    if isinstance(exc, WhatsAppGraphError) and exc.requires_reconnect:
                        connection.status = "reconnect_required"
                await session.commit()
                if permanent_graph_error:
                    return
        raise
    finally:
        await engine.dispose()


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="northstar.process_whatsapp_inbound",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=5,
)
def process_whatsapp_inbound(_task: object, event_id: str) -> None:
    asyncio.run(_process_whatsapp_inbound_async(UUID(event_id)))


async def _send_whatsapp_human_reply_async(delivery_id: UUID) -> None:
    engine = build_engine(get_settings())
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def run() -> None:
        async with factory() as session:
            delivery = await session.scalar(
                select(WhatsAppOutboundDelivery)
                .where(WhatsAppOutboundDelivery.id == delivery_id)
                .with_for_update()
            )
            if not delivery or delivery.status in OUTBOUND_TERMINAL_STATUSES:
                await session.rollback()
                return
            if delivery.connection_id is None:
                delivery.status = "cancelled"
                delivery.last_error = "WhatsApp connection was disconnected"
                message = await session.get(Message, delivery.message_id)
                if message:
                    message.finish_reason = "cancelled"
                await session.commit()
                return
            delivery.status = "processing"
            delivery.last_error = None
            await session.commit()
            message = await session.get(Message, delivery.message_id)
            connection = await session.get(WhatsAppConnection, delivery.connection_id)
            if not message or not connection or connection.status != "connected":
                delivery.status = "cancelled"
                delivery.last_error = "WhatsApp connection or message is unavailable"
                if message:
                    message.finish_reason = "cancelled"
                await session.commit()
                return
            if not token_expiry_is_current(connection.token_expires_at):
                connection.status = "reconnect_required"
                delivery.status = "reconnect_required"
                delivery.last_error = "WhatsApp access token expired"
                message.finish_reason = "failed"
                await session.commit()
                return
            latest_customer_message = await session.scalar(
                select(
                    func.max(
                        func.coalesce(
                            WhatsAppInboundMessage.provider_timestamp,
                            WhatsAppInboundMessage.created_at,
                        )
                    )
                ).where(
                    WhatsAppInboundMessage.tenant_id == delivery.tenant_id,
                    WhatsAppInboundMessage.connection_id == delivery.connection_id,
                    WhatsAppInboundMessage.conversation_id == message.conversation_id,
                    WhatsAppInboundMessage.status.not_in(("ignored", "cancelled")),
                    WhatsAppInboundMessage.message_text != "",
                )
            )
            if latest_customer_message is not None and latest_customer_message.tzinfo is None:
                latest_customer_message = latest_customer_message.replace(tzinfo=UTC)
            now = datetime.now(UTC)
            if (
                latest_customer_message is None
                or latest_customer_message < now - timedelta(hours=24)
                or latest_customer_message > now + timedelta(minutes=5)
            ):
                delivery.status = "rejected"
                delivery.last_error = "24-hour customer-service window is closed"
                message.finish_reason = "rejected"
                await session.commit()
                return
            access_token = token_cipher.decrypt(connection.access_token_encrypted)
            chunks = split_whatsapp_text(message.content)
            sent_ids = list(delivery.provider_message_ids or [])
            for chunk in chunks[len(sent_ids) :]:
                provider_id = await whatsapp_client.send_text(
                    access_token,
                    phone_number_id=connection.phone_number_id,
                    to=delivery.recipient_wa_id,
                    text=chunk,
                )
                sent_ids.append(provider_id)
                delivery.provider_message_ids = list(sent_ids)
                await session.commit()
            delivery.status = "sent"
            delivery.processed_at = datetime.now(UTC)
            message.finish_reason = "sent"
            connection.last_error = None
            await session.commit()

    try:
        if engine.dialect.name != "postgresql":
            await run()
        else:
            async with factory() as lookup_session:
                lock_delivery = await lookup_session.get(WhatsAppOutboundDelivery, delivery_id)
                if (
                    not lock_delivery
                    or lock_delivery.status in OUTBOUND_TERMINAL_STATUSES
                    or lock_delivery.connection_id is None
                ):
                    return
                lock_key = whatsapp_thread_lock_key(
                    lock_delivery.connection_id, lock_delivery.recipient_wa_id
                )
            async with engine.connect() as lock_connection:
                async with lock_connection.begin():
                    await lock_connection.execute(
                        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                        {"key": lock_key},
                    )
                    await run()
    except Exception as exc:
        async with factory() as session:
            delivery = await session.get(WhatsAppOutboundDelivery, delivery_id)
            if delivery and delivery.status not in OUTBOUND_TERMINAL_STATUSES:
                permanent_graph_error = isinstance(exc, WhatsAppGraphError) and not exc.retryable
                delivery.status = (
                    "reconnect_required"
                    if isinstance(exc, WhatsAppGraphError) and exc.requires_reconnect
                    else "rejected"
                    if permanent_graph_error
                    else "failed"
                )
                delivery.last_error = f"delivery failed: {type(exc).__name__}"[:500]
                message = await session.get(Message, delivery.message_id)
                if message:
                    message.finish_reason = "failed"
                connection = (
                    await session.get(WhatsAppConnection, delivery.connection_id)
                    if delivery.connection_id is not None
                    else None
                )
                if connection and isinstance(exc, WhatsAppGraphError) and exc.requires_reconnect:
                    connection.status = "reconnect_required"
                await session.commit()
                if permanent_graph_error:
                    return
        raise
    finally:
        await engine.dispose()


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="northstar.send_whatsapp_human_reply",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=5,
)
def send_whatsapp_human_reply(_task: object, delivery_id: str) -> None:
    asyncio.run(_send_whatsapp_human_reply_async(UUID(delivery_id)))


@task_failure.connect  # type: ignore[untyped-decorator]
def report_final_failure(
    task_id: object | None = None,
    exception: BaseException | None = None,
    sender: object | None = None,
    **_: Any,
) -> None:
    task_name = str(getattr(sender, "name", "unknown"))
    if task_name == "northstar.ingest_source":
        INGESTION_TASKS.labels("failure").inc()
    logger.error(
        "celery_task_failed",
        task_id=str(task_id),
        task=task_name,
        error=type(exception).__name__ if exception else "unknown",
    )
