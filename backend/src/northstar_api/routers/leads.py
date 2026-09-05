from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from northstar_api.database import get_session
from northstar_api.dependencies import CurrentPrincipal, WorkspaceWritePrincipal
from northstar_api.models import Agent, Conversation, Lead
from northstar_api.schemas import LeadCreate, LeadOut, LeadPatch, PageResult
from northstar_api.services.outbox import enqueue_event

router = APIRouter(prefix="/leads", tags=["leads"])
DB = Annotated[AsyncSession, Depends(get_session)]


def lead_response(lead: Lead) -> LeadOut:
    return LeadOut(
        id=lead.id,
        agent_id=lead.agent_id,
        conversation_id=lead.conversation_id,
        name=lead.name,
        email=lead.email,
        phone=lead.phone,
        status=lead.status,
        consent=lead.consent,
        fields=lead.fields_json,
        created_at=lead.created_at,
        updated_at=lead.updated_at,
    )


@router.get("", response_model=PageResult[LeadOut])
async def list_leads(
    principal: CurrentPrincipal,
    session: DB,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> PageResult[LeadOut]:
    condition = Lead.tenant_id == principal.tenant_id
    total = int(await session.scalar(select(func.count()).select_from(Lead).where(condition)) or 0)
    leads = (
        await session.scalars(
            select(Lead)
            .where(condition)
            .order_by(Lead.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return PageResult(
        items=[lead_response(item) for item in leads], total=total, page=page, page_size=page_size
    )


@router.post("", response_model=LeadOut, status_code=201)
async def create_lead(payload: LeadCreate, principal: WorkspaceWritePrincipal, session: DB) -> LeadOut:
    agent = await session.scalar(
        select(Agent).where(Agent.id == payload.agent_id, Agent.tenant_id == principal.tenant_id)
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if payload.conversation_id:
        conversation = await session.scalar(
            select(Conversation).where(
                Conversation.id == payload.conversation_id,
                Conversation.tenant_id == principal.tenant_id,
                Conversation.agent_id == payload.agent_id,
            )
        )
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
    lead = Lead(
        tenant_id=principal.tenant_id,
        agent_id=payload.agent_id,
        conversation_id=payload.conversation_id,
        name=payload.name,
        email=str(payload.email) if payload.email else None,
        phone=payload.phone,
        consent=payload.consent,
        fields_json=payload.fields,
    )
    session.add(lead)
    await session.flush()
    enqueue_event(
        session,
        tenant_id=principal.tenant_id,
        aggregate_type="lead",
        aggregate_id=lead.id,
        event_type="lead.captured.v1",
        payload={"leadId": str(lead.id), "agentId": str(lead.agent_id)},
    )
    await session.commit()
    return lead_response(lead)


@router.patch("/{lead_id}", response_model=LeadOut)
async def update_lead(
    lead_id: UUID,
    payload: LeadPatch,
    principal: WorkspaceWritePrincipal,
    session: DB,
) -> LeadOut:
    lead = await session.scalar(select(Lead).where(Lead.id == lead_id, Lead.tenant_id == principal.tenant_id))
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead.status = payload.status
    await session.commit()
    return lead_response(lead)
