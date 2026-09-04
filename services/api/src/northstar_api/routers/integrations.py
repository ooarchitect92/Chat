from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from redis.exceptions import RedisError
from sqlalchemy import delete, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from northstar_api.config import get_settings
from northstar_api.database import engine, get_session
from northstar_api.dependencies import AdminPrincipal, CurrentPrincipal
from northstar_api.models import (
    Agent,
    AgentStatus,
    Channel,
    Conversation,
    ConversationState,
    IntegrationConnection,
    Message,
    WhatsAppConnection,
    WhatsAppInboundMessage,
    WhatsAppOutboundDelivery,
)
from northstar_api.schemas import (
    IntegrationOut,
    IntegrationPatch,
    WhatsAppBootstrapOut,
    WhatsAppCompleteRequest,
    WhatsAppConnectionOut,
    WhatsAppStatusOut,
)
from northstar_api.security import create_whatsapp_signup_token, decode_token
from northstar_api.services.outbox import enqueue_event
from northstar_api.services.rate_limit import redis_services
from northstar_api.services.whatsapp import (
    WhatsAppConfigurationError,
    WhatsAppGraphError,
    token_cipher,
    token_expiry_is_current,
    whatsapp_client,
)
from northstar_api.workers.tasks import whatsapp_thread_lock_key

router = APIRouter(prefix="/integrations", tags=["integrations"])
DB = Annotated[AsyncSession, Depends(get_session)]
_local_setup_lock = asyncio.Lock()

CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "website",
        "name": "Website widget",
        "description": "Embed the agent on any website.",
        "category": "channel",
        "icon": "code",
    },
    {
        "id": "slack",
        "name": "Slack",
        "description": "Answer questions in Slack.",
        "category": "channel",
        "icon": "hash",
    },
    {
        "id": "whatsapp",
        "name": "WhatsApp",
        "description": "Support WhatsApp Business customers.",
        "category": "channel",
        "icon": "message",
    },
    {
        "id": "zapier",
        "name": "Zapier",
        "description": "Trigger workflows from events.",
        "category": "automation",
        "icon": "zap",
    },
    {
        "id": "notion",
        "name": "Notion",
        "description": "Sync selected knowledge pages.",
        "category": "data",
        "icon": "book",
    },
    {
        "id": "api",
        "name": "Developer API",
        "description": "Use REST and streaming APIs.",
        "category": "developer",
        "icon": "terminal",
    },
    {
        "id": "teams",
        "name": "Microsoft Teams",
        "description": "Bring answers into Teams.",
        "category": "channel",
        "icon": "users",
        "comingSoon": True,
    },
)


def whatsapp_setup_lock_keys(tenant_id: UUID, phone_number_id: str) -> tuple[str, str]:
    # The category order is invariant across callers, preventing lock cycles.
    return (f"whatsapp-setup:tenant:{tenant_id}", f"whatsapp-setup:phone:{phone_number_id}")


async def serialize_whatsapp_setup(
    payload: WhatsAppCompleteRequest,
    principal: AdminPrincipal,
) -> AsyncIterator[None]:
    keys = whatsapp_setup_lock_keys(principal.tenant_id, payload.phone_number_id)
    if engine.dialect.name == "postgresql":
        async with engine.connect() as lock_connection:
            async with lock_connection.begin():
                for key in keys:
                    locked = bool(
                        await lock_connection.scalar(
                            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
                            {"key": key},
                        )
                    )
                    if not locked:
                        raise HTTPException(
                            status_code=409,
                            detail="Another WhatsApp setup is already in progress",
                        )
                yield
        return

    if _local_setup_lock.locked():
        raise HTTPException(status_code=409, detail="Another WhatsApp setup is already in progress")
    async with _local_setup_lock:
        yield


WhatsAppSetupLock = Annotated[None, Depends(serialize_whatsapp_setup)]


async def integration_list(session: AsyncSession, tenant_id: UUID) -> list[IntegrationOut]:
    states = {
        item.integration_id: item.connected
        for item in (
            await session.scalars(
                select(IntegrationConnection).where(IntegrationConnection.tenant_id == tenant_id)
            )
        ).all()
    }
    whatsapp = await session.scalar(
        select(WhatsAppConnection).where(
            WhatsAppConnection.tenant_id == tenant_id,
            WhatsAppConnection.status == "connected",
        )
    )
    states["whatsapp"] = bool(whatsapp and _token_is_current(whatsapp))
    return [
        IntegrationOut.model_validate(
            {
                **item,
                "connected": states.get(item["id"], item["id"] in {"website", "api"}),
                "comingSoon": item.get("comingSoon", False),
            }
        )
        for item in CATALOG
    ]


@router.get("", response_model=list[IntegrationOut])
async def list_integrations(principal: CurrentPrincipal, session: DB) -> list[IntegrationOut]:
    return await integration_list(session, principal.tenant_id)


@router.patch("/{integration_id}", response_model=IntegrationOut)
async def update_integration(
    integration_id: str, payload: IntegrationPatch, principal: AdminPrincipal, session: DB
) -> IntegrationOut:
    catalog_entry = next((item for item in CATALOG if item["id"] == integration_id), None)
    if not catalog_entry:
        raise HTTPException(status_code=404, detail="Integration not found")
    if catalog_entry.get("comingSoon"):
        raise HTTPException(status_code=409, detail="Integration is not available yet")
    if integration_id == "whatsapp":
        raise HTTPException(
            status_code=409,
            detail="Use WhatsApp Embedded Signup to connect or disconnect a phone number",
        )
    connection = await session.scalar(
        select(IntegrationConnection).where(
            IntegrationConnection.tenant_id == principal.tenant_id,
            IntegrationConnection.integration_id == integration_id,
        )
    )
    if not connection:
        connection = IntegrationConnection(
            tenant_id=principal.tenant_id, integration_id=integration_id, connected=payload.connected
        )
        session.add(connection)
    else:
        connection.connected = payload.connected
    enqueue_event(
        session,
        tenant_id=principal.tenant_id,
        aggregate_type="integration",
        aggregate_id=integration_id,
        event_type="integration.connection.changed.v1",
        payload={"integrationId": integration_id, "connected": payload.connected},
    )
    await session.commit()
    return IntegrationOut.model_validate(
        {
            **catalog_entry,
            "connected": payload.connected,
            "comingSoon": catalog_entry.get("comingSoon", False),
        }
    )


def _token_is_current(connection: WhatsAppConnection) -> bool:
    return token_expiry_is_current(connection.token_expires_at)


def whatsapp_connection_response(
    connection: WhatsAppConnection, *, status_override: str | None = None
) -> WhatsAppConnectionOut:
    return WhatsAppConnectionOut(
        waba_id=connection.waba_id,
        phone_number_id=connection.phone_number_id,
        display_phone_number=connection.display_phone_number,
        verified_name=connection.verified_name,
        agent_id=connection.agent_id,
        status=status_override or connection.status,
        token_expires_at=connection.token_expires_at,
        connected_at=connection.connected_at,
    )


async def whatsapp_status(session: AsyncSession, tenant_id: UUID) -> WhatsAppStatusOut:
    settings = get_settings()
    connection = await session.scalar(
        select(WhatsAppConnection).where(WhatsAppConnection.tenant_id == tenant_id)
    )
    token_current = bool(connection and connection.status == "connected" and _token_is_current(connection))
    connection_status = None
    if connection:
        connection_status = (
            "reconnect_required"
            if connection.status == "connected" and not token_current
            else connection.status
        )
    return WhatsAppStatusOut(
        enabled=settings.whatsapp_configured,
        connected=token_current,
        connection=(
            whatsapp_connection_response(
                connection,
                status_override=connection_status,
            )
            if connection
            else None
        ),
    )


@router.get("/whatsapp/bootstrap", response_model=WhatsAppBootstrapOut)
async def whatsapp_bootstrap(principal: AdminPrincipal, session: DB) -> WhatsAppBootstrapOut:
    settings = get_settings()
    status = await whatsapp_status(session, principal.tenant_id)
    signup_session = None
    if status.enabled:
        signup_session, _ = create_whatsapp_signup_token(principal.user_id, principal.tenant_id, settings)
    return WhatsAppBootstrapOut(
        enabled=status.enabled,
        connected=status.connected,
        connection=status.connection,
        app_id=settings.meta_app_id if status.enabled else None,
        configuration_id=(settings.meta_whatsapp_configuration_id if status.enabled else None),
        api_version=settings.meta_graph_api_version,
        signup_session=signup_session,
    )


@router.get("/whatsapp/status", response_model=WhatsAppStatusOut)
async def get_whatsapp_status(principal: CurrentPrincipal, session: DB) -> WhatsAppStatusOut:
    return await whatsapp_status(session, principal.tenant_id)


@router.post("/whatsapp/complete", response_model=WhatsAppConnectionOut)
async def complete_whatsapp_signup(
    payload: WhatsAppCompleteRequest,
    principal: AdminPrincipal,
    session: DB,
    _setup_lock: WhatsAppSetupLock,
) -> WhatsAppConnectionOut:
    settings = get_settings()
    if not settings.whatsapp_configured:
        raise HTTPException(status_code=503, detail="WhatsApp integration is not configured")
    await _consume_whatsapp_signup_session(
        payload.signup_session,
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
    )
    agent = await session.scalar(
        select(Agent).where(
            Agent.id == payload.agent_id,
            Agent.tenant_id == principal.tenant_id,
            Agent.deleted_at.is_(None),
        )
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.status != AgentStatus.ACTIVE:
        raise HTTPException(
            status_code=409,
            detail="Publish the agent before connecting it to WhatsApp",
        )
    current_connection = await session.scalar(
        select(WhatsAppConnection).where(WhatsAppConnection.tenant_id == principal.tenant_id)
    )
    if current_connection and (
        current_connection.waba_id != payload.waba_id
        or current_connection.phone_number_id != payload.phone_number_id
    ):
        raise HTTPException(
            status_code=409,
            detail="Disconnect the current WhatsApp number before selecting a different one",
        )
    if session.bind and session.bind.dialect.name == "postgresql":
        phone_owner = await session.scalar(
            text("SELECT northstar_find_whatsapp_phone_owner(:phone_number_id)"),
            {"phone_number_id": payload.phone_number_id},
        )
    else:
        phone_owner = await session.scalar(
            select(WhatsAppConnection.tenant_id).where(
                WhatsAppConnection.phone_number_id == payload.phone_number_id
            )
        )
    if phone_owner is not None and UUID(str(phone_owner)) != principal.tenant_id:
        raise HTTPException(
            status_code=409,
            detail="That WhatsApp phone number is already connected to another workspace",
        )
    try:
        setup = await whatsapp_client.complete_setup(
            code=payload.code,
            waba_id=payload.waba_id,
            phone_number_id=payload.phone_number_id,
            registration_pin=payload.two_step_verification_pin,
        )
        encrypted_token = token_cipher.encrypt(setup.access_token)
    except WhatsAppConfigurationError:
        raise HTTPException(status_code=503, detail="WhatsApp integration is not configured") from None
    except WhatsAppGraphError:
        raise HTTPException(
            status_code=502,
            detail="Meta could not verify or connect the selected WhatsApp number",
        ) from None

    connection = current_connection
    if not connection:
        connection = WhatsAppConnection(
            tenant_id=principal.tenant_id,
            agent_id=payload.agent_id,
            waba_id=payload.waba_id,
            phone_number_id=payload.phone_number_id,
            display_phone_number=setup.display_phone_number,
            verified_name=setup.verified_name,
            access_token_encrypted=encrypted_token,
            token_expires_at=setup.expires_at,
            status="connected",
            connected_at=datetime.now(UTC),
        )
        session.add(connection)
    else:
        connection.agent_id = payload.agent_id
        connection.waba_id = payload.waba_id
        connection.phone_number_id = payload.phone_number_id
        connection.display_phone_number = setup.display_phone_number
        connection.verified_name = setup.verified_name
        connection.access_token_encrypted = encrypted_token
        connection.token_expires_at = setup.expires_at
        connection.status = "connected"
        connection.last_error = None
        connection.connected_at = datetime.now(UTC)

    integration = await session.scalar(
        select(IntegrationConnection).where(
            IntegrationConnection.tenant_id == principal.tenant_id,
            IntegrationConnection.integration_id == "whatsapp",
        )
    )
    if not integration:
        integration = IntegrationConnection(
            tenant_id=principal.tenant_id,
            integration_id="whatsapp",
            connected=True,
        )
        session.add(integration)
    else:
        integration.connected = True
        integration.config_encrypted = None
    enqueue_event(
        session,
        tenant_id=principal.tenant_id,
        aggregate_type="integration",
        aggregate_id="whatsapp",
        event_type="integration.connection.changed.v1",
        payload={
            "integrationId": "whatsapp",
            "connected": True,
            "agentId": str(payload.agent_id),
            "phoneNumberId": payload.phone_number_id,
        },
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="That WhatsApp phone number is already connected to another workspace",
        ) from None
    await session.refresh(connection)
    return whatsapp_connection_response(connection)


async def _consume_whatsapp_signup_session(
    signup_session: str,
    *,
    user_id: UUID,
    tenant_id: UUID,
) -> None:
    settings = get_settings()
    try:
        claims = decode_token(signup_session, "whatsapp_signup", settings)
        if UUID(str(claims["sub"])) != user_id or UUID(str(claims["tid"])) != tenant_id:
            raise ValueError("signup session principal mismatch")
        token_id = str(claims["jti"])
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=400, detail="WhatsApp signup session is invalid or expired") from None
    try:
        consumed = await redis_services.client.set(
            f"northstar:whatsapp-signup:{token_id}",
            "consumed",
            ex=settings.meta_signup_session_ttl_seconds,
            nx=True,
        )
    except RedisError:
        if not settings.rate_limit_fail_open:
            raise HTTPException(
                status_code=503,
                detail="Signup session service is temporarily unavailable",
            ) from None
        return
    if not consumed:
        raise HTTPException(status_code=409, detail="WhatsApp signup session was already used")


@router.delete("/whatsapp", status_code=204)
async def disconnect_whatsapp(principal: AdminPrincipal, session: DB) -> None:
    if engine.dialect.name == "postgresql":
        async with engine.connect() as setup_lock_connection:
            async with setup_lock_connection.begin():
                tenant_lock_key = whatsapp_setup_lock_keys(principal.tenant_id, "unused")[0]
                await setup_lock_connection.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": tenant_lock_key},
                )
                await _disconnect_whatsapp_locked(principal, session, setup_lock_connection)
        return

    async with _local_setup_lock:
        await _disconnect_whatsapp_locked(principal, session)


async def _disconnect_whatsapp_locked(
    principal: AdminPrincipal,
    session: AsyncSession,
    setup_lock_connection: AsyncConnection | None = None,
) -> None:
    # The tenant setup lock is already held, so this read is stable against
    # Embedded Signup. Acquire the phone setup lock second, matching /complete.
    connection = await session.scalar(
        select(WhatsAppConnection).where(WhatsAppConnection.tenant_id == principal.tenant_id)
    )
    if not connection:
        return
    if setup_lock_connection is not None:
        phone_lock_key = whatsapp_setup_lock_keys(principal.tenant_id, connection.phone_number_id)[1]
        await setup_lock_connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": phone_lock_key},
        )

    connection = await session.scalar(
        select(WhatsAppConnection)
        .where(
            WhatsAppConnection.id == connection.id,
            WhatsAppConnection.tenant_id == principal.tenant_id,
        )
        .with_for_update()
    )
    if not connection:
        return
    connection.status = "disconnecting"
    connection.last_error = None
    await session.commit()

    # Phase two intentionally starts only after the status transition commits
    # and its row lock is released. Workers hold per-thread advisory locks, so
    # this lock order cannot cycle with their final connection updates.
    transient_statuses = ("queued", "failed", "processing")
    inbound_senders = set(
        await session.scalars(
            select(WhatsAppInboundMessage.sender_wa_id).where(
                WhatsAppInboundMessage.connection_id == connection.id,
                WhatsAppInboundMessage.status.in_(transient_statuses),
            )
        )
    )
    outbound_recipients = set(
        await session.scalars(
            select(WhatsAppOutboundDelivery.recipient_wa_id).where(
                WhatsAppOutboundDelivery.connection_id == connection.id,
                WhatsAppOutboundDelivery.status.in_(transient_statuses),
            )
        )
    )
    if setup_lock_connection is not None:
        # Workers hold this same transaction-scoped lock while talking to Meta.
        # Waiting here prevents a send from racing credential deletion.
        for recipient in sorted(inbound_senders | outbound_recipients):
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": whatsapp_thread_lock_key(connection.id, recipient)},
            )

    connection = await session.scalar(
        select(WhatsAppConnection)
        .where(
            WhatsAppConnection.id == connection.id,
            WhatsAppConnection.tenant_id == principal.tenant_id,
        )
        .with_for_update()
    )
    if not connection:
        await session.rollback()
        return

    active_message_ids = select(WhatsAppOutboundDelivery.message_id).where(
        WhatsAppOutboundDelivery.connection_id == connection.id,
        WhatsAppOutboundDelivery.status.in_(transient_statuses),
    )
    await session.execute(
        update(Message)
        .where(
            Message.tenant_id == principal.tenant_id,
            Message.id.in_(active_message_ids),
        )
        .values(finish_reason="cancelled")
    )
    affected_conversation_ids = select(WhatsAppInboundMessage.conversation_id).where(
        WhatsAppInboundMessage.connection_id == connection.id,
        WhatsAppInboundMessage.conversation_id.is_not(None),
    )
    observed_sender_ids = select(WhatsAppInboundMessage.sender_wa_id).where(
        WhatsAppInboundMessage.connection_id == connection.id
    )
    disconnected_at = datetime.now(UTC)
    await session.execute(
        update(Conversation)
        .where(
            Conversation.tenant_id == principal.tenant_id,
            Conversation.agent_id == connection.agent_id,
            Conversation.channel == Channel.WHATSAPP,
            Conversation.state != ConversationState.RESOLVED,
            or_(
                Conversation.id.in_(affected_conversation_ids),
                Conversation.visitor_id.in_(observed_sender_ids),
            ),
        )
        .values(
            state=ConversationState.RESOLVED,
            updated_at=disconnected_at,
            ended_at=disconnected_at,
            resolution="whatsapp_disconnected",
        )
    )
    await session.execute(
        update(WhatsAppOutboundDelivery)
        .where(
            WhatsAppOutboundDelivery.connection_id == connection.id,
            WhatsAppOutboundDelivery.status.in_(transient_statuses),
        )
        .values(
            connection_id=None,
            status="cancelled",
            next_dispatch_at=None,
            last_error="WhatsApp disconnected",
        )
    )
    await session.execute(
        update(WhatsAppInboundMessage)
        .where(
            WhatsAppInboundMessage.connection_id == connection.id,
            WhatsAppInboundMessage.status.in_(transient_statuses),
        )
        .values(
            connection_id=None,
            status="cancelled",
            next_dispatch_at=None,
            last_error="WhatsApp disconnected",
        )
    )
    # Explicitly detach every audit record as well as relying on ON DELETE SET
    # NULL, which also keeps local SQLite/dev deployments deterministic.
    await session.execute(
        update(WhatsAppOutboundDelivery)
        .where(WhatsAppOutboundDelivery.connection_id == connection.id)
        .values(connection_id=None)
    )
    await session.execute(
        update(WhatsAppInboundMessage)
        .where(WhatsAppInboundMessage.connection_id == connection.id)
        .values(connection_id=None)
    )
    # Do not deregister the customer's phone, revoke Facebook permissions, or
    # unsubscribe the whole WABA: those resources may be shared by other phone
    # numbers. Deleting the locally encrypted credential is authoritative. The
    # nullable audit links preserve Meta message-id idempotency across reconnects.
    await session.execute(delete(WhatsAppConnection).where(WhatsAppConnection.id == connection.id))
    integration = await session.scalar(
        select(IntegrationConnection).where(
            IntegrationConnection.tenant_id == principal.tenant_id,
            IntegrationConnection.integration_id == "whatsapp",
        )
    )
    if integration:
        integration.connected = False
        integration.config_encrypted = None
    enqueue_event(
        session,
        tenant_id=principal.tenant_id,
        aggregate_type="integration",
        aggregate_id="whatsapp",
        event_type="integration.connection.changed.v1",
        payload={"integrationId": "whatsapp", "connected": False},
    )
    await session.commit()
