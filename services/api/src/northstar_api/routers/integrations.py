from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from northstar_api.database import get_session
from northstar_api.dependencies import AdminPrincipal, CurrentPrincipal
from northstar_api.models import IntegrationConnection
from northstar_api.schemas import IntegrationOut, IntegrationPatch
from northstar_api.services.outbox import enqueue_event

router = APIRouter(prefix="/integrations", tags=["integrations"])
DB = Annotated[AsyncSession, Depends(get_session)]

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


async def integration_list(session: AsyncSession, tenant_id: UUID) -> list[IntegrationOut]:
    states = {
        item.integration_id: item.connected
        for item in (
            await session.scalars(
                select(IntegrationConnection).where(IntegrationConnection.tenant_id == tenant_id)
            )
        ).all()
    }
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
