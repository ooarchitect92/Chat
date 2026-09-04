from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Load, selectinload

from northstar_api.config import get_settings
from northstar_api.database import get_session
from northstar_api.dependencies import CurrentPrincipal, WorkspaceWritePrincipal
from northstar_api.models import (
    Channel,
    Conversation,
    Message,
    MessageRole,
    WhatsAppConnection,
    WhatsAppInboundMessage,
    WhatsAppOutboundDelivery,
)
from northstar_api.schemas import (
    CitationOut,
    ConversationOut,
    ConversationPatch,
    ConversationReplyCreate,
    MessageOut,
    PageResult,
)
from northstar_api.services.outbox import enqueue_event
from northstar_api.services.whatsapp import token_expiry_is_current
from northstar_api.workers.tasks import send_whatsapp_human_reply

router = APIRouter(prefix="/conversations", tags=["conversations"])
DB = Annotated[AsyncSession, Depends(get_session)]


def conversation_response(conversation: Conversation) -> ConversationOut:
    messages = [
        MessageOut(
            id=message.id,
            role=message.role,
            content=message.content,
            created_at=message.created_at,
            citations=[CitationOut(title=item.title, url=item.url) for item in message.citations],
        )
        for message in conversation.messages
    ]
    preview = messages[-1].content[:240] if messages else "No messages yet"
    return ConversationOut(
        id=conversation.id,
        agent_id=conversation.agent_id,
        visitor_name=conversation.visitor_name,
        visitor_email=conversation.visitor_email,
        channel=conversation.channel,
        state=conversation.state,
        sentiment=conversation.sentiment,
        preview=preview,
        unread=conversation.unread_count,
        started_at=conversation.started_at,
        updated_at=conversation.updated_at,
        messages=messages,
    )


def conversation_options() -> Load:
    # SQLAlchemy's public type hint is the abstract loader base, while this
    # factory returns a concrete Load instance at runtime.
    return cast(Load, selectinload(Conversation.messages).selectinload(Message.citations))


@router.get("", response_model=PageResult[ConversationOut])
async def list_conversations(
    principal: CurrentPrincipal,
    session: DB,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> PageResult[ConversationOut]:
    condition = Conversation.tenant_id == principal.tenant_id
    total = int(await session.scalar(select(func.count()).select_from(Conversation).where(condition)) or 0)
    rows = (
        await session.scalars(
            select(Conversation)
            .options(conversation_options())
            .where(condition)
            .order_by(Conversation.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return PageResult(
        items=[conversation_response(item) for item in rows], total=total, page=page, page_size=page_size
    )


@router.get("/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    conversation_id: UUID, principal: CurrentPrincipal, session: DB
) -> ConversationOut:
    conversation = await session.scalar(
        select(Conversation)
        .options(conversation_options())
        .where(Conversation.id == conversation_id, Conversation.tenant_id == principal.tenant_id)
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation_response(conversation)


@router.patch("/{conversation_id}", response_model=ConversationOut)
async def update_conversation(
    conversation_id: UUID,
    payload: ConversationPatch,
    principal: WorkspaceWritePrincipal,
    session: DB,
) -> ConversationOut:
    conversation = await session.scalar(
        select(Conversation)
        .options(conversation_options())
        .where(Conversation.id == conversation_id, Conversation.tenant_id == principal.tenant_id)
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    previous_state = conversation.state
    for name, value in payload.model_dump(exclude_unset=True).items():
        setattr(conversation, "unread_count" if name == "unread" else name, value)
    if payload.state and payload.state != previous_state:
        conversation.ended_at = datetime.now(UTC) if payload.state.value == "resolved" else None
        enqueue_event(
            session,
            tenant_id=principal.tenant_id,
            aggregate_type="conversation",
            aggregate_id=conversation.id,
            event_type="conversation.state.changed.v1",
            payload={
                "conversationId": str(conversation.id),
                "agentId": str(conversation.agent_id),
                "previousState": previous_state.value,
                "state": conversation.state.value,
            },
        )
    await session.commit()
    await session.refresh(conversation)
    return conversation_response(conversation)


@router.post("/{conversation_id}/messages", response_model=MessageOut, status_code=201)
async def reply_to_conversation(
    conversation_id: UUID,
    payload: ConversationReplyCreate,
    principal: WorkspaceWritePrincipal,
    session: DB,
) -> MessageOut:
    conversation = await session.scalar(
        select(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == principal.tenant_id,
        )
        .with_for_update()
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    whatsapp_connection: WhatsAppConnection | None = None
    if conversation.channel == Channel.WHATSAPP:
        if not conversation.visitor_id or not conversation.visitor_id.isdigit():
            raise HTTPException(status_code=409, detail="WhatsApp recipient is unavailable")
        whatsapp_connection = await session.scalar(
            select(WhatsAppConnection)
            .where(
                WhatsAppConnection.tenant_id == principal.tenant_id,
                WhatsAppConnection.agent_id == conversation.agent_id,
                WhatsAppConnection.status == "connected",
            )
            .with_for_update()
        )
        if not whatsapp_connection:
            raise HTTPException(status_code=409, detail="WhatsApp is not connected for this agent")
        if not token_expiry_is_current(whatsapp_connection.token_expires_at):
            raise HTTPException(
                status_code=409,
                detail="WhatsApp authorization expired; reconnect the integration",
            )
        latest_customer_message = await session.scalar(
            select(
                func.max(
                    func.coalesce(
                        WhatsAppInboundMessage.provider_timestamp,
                        WhatsAppInboundMessage.created_at,
                    )
                )
            ).where(
                WhatsAppInboundMessage.tenant_id == principal.tenant_id,
                WhatsAppInboundMessage.connection_id == whatsapp_connection.id,
                WhatsAppInboundMessage.conversation_id == conversation.id,
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
            raise HTTPException(
                status_code=409,
                detail=(
                    "The 24-hour WhatsApp customer-service window is closed; "
                    "send an approved template instead"
                ),
            )
    sequence = int(
        await session.scalar(
            select(func.coalesce(func.max(Message.sequence), 0)).where(
                Message.conversation_id == conversation.id,
                Message.tenant_id == principal.tenant_id,
            )
        )
        or 0
    )
    message = Message(
        tenant_id=principal.tenant_id,
        conversation_id=conversation.id,
        sequence=sequence + 1,
        role=MessageRole.AGENT,
        content=payload.content.strip(),
        finish_reason="queued" if whatsapp_connection else "sent",
    )
    session.add(message)
    conversation.updated_at = datetime.now(UTC)
    await session.flush()
    delivery: WhatsAppOutboundDelivery | None = None
    if whatsapp_connection:
        delivery = WhatsAppOutboundDelivery(
            tenant_id=principal.tenant_id,
            connection_id=whatsapp_connection.id,
            phone_number_id=whatsapp_connection.phone_number_id,
            message_id=message.id,
            recipient_wa_id=conversation.visitor_id or "",
            status="queued",
        )
        session.add(delivery)
        await session.flush()
    enqueue_event(
        session,
        tenant_id=principal.tenant_id,
        aggregate_type="conversation",
        aggregate_id=conversation.id,
        event_type="chat.message.created.v1",
        payload={
            "conversationId": str(conversation.id),
            "agentId": str(conversation.agent_id),
            "messageId": str(message.id),
            "role": "agent",
        },
    )
    await session.commit()
    if delivery:
        try:
            await asyncio.to_thread(
                send_whatsapp_human_reply.apply_async,
                args=[str(delivery.id)],
                queue="whatsapp.outbound",
                routing_key="whatsapp.outbound",
            )
            delivery.dispatch_attempts += 1
            delivery.dispatched_at = datetime.now(UTC)
            delivery.next_dispatch_at = datetime.now(UTC) + timedelta(minutes=5)
            delivery.last_error = None
        except Exception:
            # The durable dispatcher will retry this database-backed delivery.
            delivery.dispatch_attempts += 1
            exhausted = delivery.dispatch_attempts >= get_settings().whatsapp_dispatch_max_attempts
            delivery.status = "dispatch_failed" if exhausted else "failed"
            delivery.next_dispatch_at = None if exhausted else datetime.now(UTC)
            delivery.last_error = "queue dispatch unavailable"
        await session.commit()
    await session.refresh(message)
    return MessageOut(
        id=message.id,
        role=message.role,
        content=message.content,
        created_at=message.created_at,
        citations=[],
    )
