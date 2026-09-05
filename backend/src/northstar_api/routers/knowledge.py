from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from northstar_api.config import get_settings
from northstar_api.database import get_session
from northstar_api.dependencies import AdminPrincipal, CurrentPrincipal
from northstar_api.models import (
    FactStatus,
    IngestionJob,
    JobStatus,
    KnowledgeFact,
    KnowledgeKind,
    KnowledgeSource,
    KnowledgeStatus,
)
from northstar_api.routers.agents import scoped_agent
from northstar_api.schemas import (
    FactCreate,
    FactOut,
    FactPatch,
    JobOut,
    KnowledgeCreate,
    KnowledgeOut,
    UploadPresignOut,
    UploadPresignRequest,
)
from northstar_api.services.ingestion import ingest_source_async
from northstar_api.services.llm import nvidia_adapter
from northstar_api.services.object_store import InvalidUpload, ObjectStoreUnavailable, object_store
from northstar_api.services.outbox import enqueue_event

router = APIRouter(tags=["knowledge"])
DB = Annotated[AsyncSession, Depends(get_session)]
logger = structlog.get_logger(__name__)


@router.post("/uploads/presign", response_model=UploadPresignOut)
async def presign_upload(payload: UploadPresignRequest, principal: AdminPrincipal) -> UploadPresignOut:
    settings = get_settings()
    if payload.size_bytes > settings.upload_max_bytes:
        raise HTTPException(status_code=413, detail="Upload exceeds the configured size limit")
    try:
        key, url, fields, expires_at = await object_store.presign_post(
            tenant_id=principal.tenant_id,
            filename=payload.filename,
            content_type=payload.content_type,
            size_bytes=payload.size_bytes,
            checksum_sha256=payload.checksum_sha256,
        )
    except InvalidUpload as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ObjectStoreUnavailable:
        raise HTTPException(status_code=503, detail="Object storage is not configured") from None
    return UploadPresignOut(url=url, object_key=key, fields=fields, expires_at=expires_at)


def source_response(source: KnowledgeSource) -> KnowledgeOut:
    return KnowledgeOut(
        id=source.id,
        agent_id=source.agent_id,
        name=source.name,
        kind=source.kind,
        status=source.status,
        size_label=source.size_label,
        chunks=source.chunk_count,
        updated_at=source.updated_at,
        url=source.url,
        content=source.content if source.kind == KnowledgeKind.TEXT else None,
        error=source.error,
    )


@router.get("/agents/{agent_id}/knowledge", response_model=list[KnowledgeOut])
async def list_knowledge(agent_id: UUID, principal: CurrentPrincipal, session: DB) -> list[KnowledgeOut]:
    await scoped_agent(session, principal.tenant_id, agent_id)
    sources = (
        await session.scalars(
            select(KnowledgeSource)
            .where(KnowledgeSource.tenant_id == principal.tenant_id, KnowledgeSource.agent_id == agent_id)
            .order_by(KnowledgeSource.updated_at.desc())
        )
    ).all()
    return [source_response(item) for item in sources]


@router.post("/agents/{agent_id}/knowledge", response_model=KnowledgeOut, status_code=201)
async def add_knowledge(
    agent_id: UUID, payload: KnowledgeCreate, principal: AdminPrincipal, session: DB
) -> KnowledgeOut:
    await scoped_agent(session, principal.tenant_id, agent_id)
    if payload.kind == KnowledgeKind.TEXT and not payload.content:
        raise HTTPException(status_code=422, detail="Text sources require content")
    if payload.kind in (KnowledgeKind.URL, KnowledgeKind.SITEMAP) and not payload.url:
        raise HTTPException(status_code=422, detail="URL and sitemap sources require a URL")
    if payload.kind == KnowledgeKind.FILE and not (payload.object_key or payload.content):
        raise HTTPException(status_code=422, detail="File sources require an uploaded object key")
    if payload.object_key and payload.kind != KnowledgeKind.FILE:
        raise HTTPException(status_code=422, detail="Only file sources can reference an uploaded object")

    object_key = payload.object_key
    uploaded_checksum: str | None = None
    uploaded_size: int | None = None
    if object_key:
        try:
            object_key, uploaded_checksum, uploaded_size = await object_store.promote_staged(
                principal.tenant_id, object_key
            )
        except InvalidUpload as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except ObjectStoreUnavailable:
            raise HTTPException(status_code=503, detail="Object storage is unavailable") from None

    raw_identity = payload.content or str(payload.url or object_key)
    source = KnowledgeSource(
        tenant_id=principal.tenant_id,
        agent_id=agent_id,
        name=payload.name.strip(),
        kind=payload.kind,
        status=KnowledgeStatus.PROCESSING,
        size_label=_size_label(uploaded_size) if uploaded_size is not None else "Queued",
        url=str(payload.url) if payload.url else None,
        content=payload.content,
        object_key=object_key,
        checksum=uploaded_checksum or hashlib.sha256(raw_identity.encode()).hexdigest(),
    )
    session.add(source)
    await session.flush()
    job = IngestionJob(
        tenant_id=principal.tenant_id,
        source_id=source.id,
        status=JobStatus.QUEUED,
        idempotency_key=f"ingest:{source.id}:{source.revision}:{source.checksum}",
    )
    session.add(job)
    enqueue_event(
        session,
        tenant_id=principal.tenant_id,
        aggregate_type="knowledge_source",
        aggregate_id=source.id,
        event_type="knowledge.source.created.v1",
        payload={"sourceId": str(source.id), "agentId": str(agent_id), "kind": source.kind.value},
    )
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        if object_key:
            try:
                await object_store.purge_exact(object_key)
            except Exception as cleanup_error:
                logger.warning(
                    "promoted_upload_cleanup_failed",
                    object_key=object_key,
                    error=type(cleanup_error).__name__,
                )
        raise

    settings = get_settings()
    if not settings.background_dispatch_enabled:
        await ingest_source_async(session, source.id)
        await session.refresh(source)
    return source_response(source)


@router.delete("/knowledge/{source_id}", status_code=204)
async def delete_knowledge(source_id: UUID, principal: AdminPrincipal, session: DB) -> Response:
    source = await session.scalar(
        select(KnowledgeSource).where(
            KnowledgeSource.id == source_id, KnowledgeSource.tenant_id == principal.tenant_id
        )
    )
    if not source:
        raise HTTPException(status_code=404, detail="Knowledge source not found")
    object_key = source.object_key
    await session.delete(source)
    enqueue_event(
        session,
        tenant_id=principal.tenant_id,
        aggregate_type="knowledge_source",
        aggregate_id=source.id,
        event_type="knowledge.source.deleted.v1",
        payload={
            "sourceId": str(source.id),
            "agentId": str(source.agent_id),
            "objectKey": object_key,
        },
    )
    await session.commit()
    return Response(status_code=204)


def _size_label(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


@router.get("/agents/{agent_id}/facts", response_model=list[FactOut])
async def list_facts(
    agent_id: UUID,
    principal: CurrentPrincipal,
    session: DB,
    fact_status: FactStatus | None = None,
) -> list[KnowledgeFact]:
    await scoped_agent(session, principal.tenant_id, agent_id)
    query = select(KnowledgeFact).where(
        KnowledgeFact.tenant_id == principal.tenant_id, KnowledgeFact.agent_id == agent_id
    )
    if fact_status:
        query = query.where(KnowledgeFact.status == fact_status)
    return list((await session.scalars(query.order_by(KnowledgeFact.updated_at.desc()))).all())


@router.post("/agents/{agent_id}/facts", response_model=FactOut, status_code=201)
async def create_fact(
    agent_id: UUID, payload: FactCreate, principal: AdminPrincipal, session: DB
) -> KnowledgeFact:
    agent = await scoped_agent(session, principal.tenant_id, agent_id)
    vector = await nvidia_adapter.embed_query(f"{payload.question}\n{payload.answer}")
    fact = KnowledgeFact(
        tenant_id=principal.tenant_id,
        agent_id=agent_id,
        question=payload.question.strip(),
        answer=payload.answer.strip(),
        status=payload.status,
        priority=payload.priority,
        revision=agent.published_knowledge_revision,
        embedding_model=get_settings().nvidia_embedding_model,
        embedding=vector,
        approved_by=principal.user_id if payload.status == FactStatus.APPROVED else None,
        approved_at=datetime.now(UTC) if payload.status == FactStatus.APPROVED else None,
    )
    session.add(fact)
    enqueue_event(
        session,
        tenant_id=principal.tenant_id,
        aggregate_type="knowledge_fact",
        aggregate_id=fact.id,
        event_type="knowledge.fact.changed.v1",
        payload={"factId": str(fact.id), "agentId": str(agent_id), "status": fact.status.value},
    )
    await session.commit()
    await session.refresh(fact)
    return fact


@router.patch("/facts/{fact_id}", response_model=FactOut)
async def update_fact(
    fact_id: UUID, payload: FactPatch, principal: AdminPrincipal, session: DB
) -> KnowledgeFact:
    fact = await session.scalar(
        select(KnowledgeFact).where(
            KnowledgeFact.id == fact_id, KnowledgeFact.tenant_id == principal.tenant_id
        )
    )
    if not fact:
        raise HTTPException(status_code=404, detail="Knowledge fact not found")
    patch = payload.model_dump(exclude_unset=True)
    for name, value in patch.items():
        setattr(fact, name, value)
    if "status" in patch and fact.status == FactStatus.APPROVED:
        fact.approved_by = principal.user_id
        fact.approved_at = datetime.now(UTC)
    if {"question", "answer"} & patch.keys():
        fact.embedding = await nvidia_adapter.embed_query(f"{fact.question}\n{fact.answer}")
        fact.embedding_model = get_settings().nvidia_embedding_model
    enqueue_event(
        session,
        tenant_id=principal.tenant_id,
        aggregate_type="knowledge_fact",
        aggregate_id=fact.id,
        event_type="knowledge.fact.changed.v1",
        payload={"factId": str(fact.id), "agentId": str(fact.agent_id), "status": fact.status.value},
    )
    await session.commit()
    await session.refresh(fact)
    return fact


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: UUID, principal: CurrentPrincipal, session: DB) -> JobOut:
    job = await session.scalar(
        select(IngestionJob).where(IngestionJob.id == job_id, IngestionJob.tenant_id == principal.tenant_id)
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobOut(
        id=job.id,
        source_id=job.source_id,
        status=job.status,
        step=job.step,
        progress=job.progress,
        attempts=job.attempts,
        error=job.error_json,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )
