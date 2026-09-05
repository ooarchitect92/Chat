from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from northstar_api.config import get_settings
from northstar_api.metrics import CHAT_DURATION, CHAT_REQUESTS
from northstar_api.models import (
    Agent,
    Channel,
    Conversation,
    Message,
    MessageCitation,
    MessageRole,
)
from northstar_api.schemas import ChatStreamRequest
from northstar_api.services.llm import ModelUnavailableError, NvidiaModelAdapter, nvidia_adapter
from northstar_api.services.outbox import enqueue_event
from northstar_api.services.rate_limit import RedisServices, redis_services
from northstar_api.services.redaction import mask_sensitive_text
from northstar_api.services.retrieval import Evidence, RetrievalService, retrieval_service


@dataclass(frozen=True, slots=True)
class PreparedChat:
    conversation_id: UUID
    assistant_message_id: UUID
    answer: str
    evidence: list[Evidence]


@dataclass(frozen=True, slots=True)
class ReservedChat:
    conversation_id: UUID
    assistant_message_id: UUID
    assistant_sequence: int
    lease_id: str


class ChatCoordinator:
    def __init__(
        self,
        retrieval: RetrievalService | None = None,
        model: NvidiaModelAdapter | None = None,
        redis: RedisServices | None = None,
    ) -> None:
        self.retrieval = retrieval or retrieval_service
        self.model = model or nvidia_adapter
        self.redis = redis or redis_services

    async def prepare(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        request: ChatStreamRequest,
        channel: Channel = Channel.WIDGET,
    ) -> PreparedChat:
        started = time.perf_counter()
        agent = await self._get_agent(session, tenant_id, request.agent_id)
        raw_message = request.message.strip()
        message_fingerprint = hashlib.sha256(raw_message.encode("utf-8")).hexdigest()
        message = (
            mask_sensitive_text(raw_message) if agent.security.get("maskSensitiveData", True) else raw_message
        )
        rate_limit = int(agent.security.get("rateLimitPerMinute", 30))
        global_limiter = await self.redis.check_rate_limit(
            f"tenant:{tenant_id}:agent:{agent.id}:global",
            get_settings().agent_global_rate_limit_per_minute,
            scope="chat_global",
        )
        if not global_limiter.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Agent message capacity exceeded",
                headers={"Retry-After": str(global_limiter.retry_after)},
            )
        limiter = await self.redis.check_rate_limit(
            f"tenant:{tenant_id}:agent:{agent.id}:visitor:{request.visitor_id or 'admin'}", rate_limit
        )
        if not limiter.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Message rate limit exceeded",
                headers={"Retry-After": str(limiter.retry_after)},
            )
        conversation = await self._get_or_create_conversation(
            session, tenant_id=tenant_id, agent=agent, request=request, channel=channel
        )
        show_citations = agent.model_profile.get("citationMode", "when-available") != "off"
        reservation = await self._reserve_exchange(
            session,
            tenant_id=tenant_id,
            agent=agent,
            conversation=conversation,
            request=request,
            message=message,
            message_fingerprint=message_fingerprint,
            show_citations=show_citations,
        )
        if isinstance(reservation, PreparedChat):
            return reservation
        try:
            conversation_history = await self._recent_history(session, reservation)
            retrieval_query = _retrieval_query(message, conversation_history)
            try:
                evidence = await self.retrieval.retrieve(
                    session,
                    tenant_id=tenant_id,
                    agent_id=agent.id,
                    revision=conversation.knowledge_revision,
                    query=retrieval_query,
                )
            except ModelUnavailableError:
                await session.rollback()
                raise HTTPException(
                    status_code=503, detail="AI retrieval is temporarily unavailable"
                ) from None
            # Retrieval is read-only; release its transaction before provider I/O.
            await session.commit()
            if not evidence:
                answer = "I don't have enough verified information to answer that."
                CHAT_REQUESTS.labels("refused_no_evidence").inc()
            else:
                evidence_block = "\n\n".join(
                    f"[S{index}] {item.text}" for index, item in enumerate(evidence, start=1)
                )
                try:
                    generated = await self.model.generate_grounded(
                        instructions=agent.instructions,
                        question=message,
                        evidence=evidence_block,
                        conversation_history=conversation_history,
                        language=agent.language,
                        tone=agent.tone.value,
                        model_profile=agent.model_profile,
                    )
                    answer = generated.content
                    CHAT_REQUESTS.labels("generated").inc()
                except ModelUnavailableError:
                    # The deterministic cited fallback is development-only. Production
                    # makes an upstream failure explicit when REQUIRE_NVIDIA is enabled.
                    if self.model.settings.require_nvidia:
                        raise HTTPException(
                            status_code=503, detail="AI provider is temporarily unavailable"
                        ) from None
                    answer = _evidence_fallback(evidence[0])
                    CHAT_REQUESTS.labels("grounded_fallback").inc()

            latency_ms = round((time.perf_counter() - started) * 1000)
            prepared = await self._complete_exchange(
                session,
                tenant_id=tenant_id,
                agent=agent,
                reservation=reservation,
                question=message,
                answer=answer,
                evidence=evidence,
                latency_ms=latency_ms,
                show_citations=show_citations,
            )
        except Exception:
            await session.rollback()
            await self._mark_reservation_failed(session, tenant_id, reservation)
            raise
        CHAT_DURATION.observe(time.perf_counter() - started)
        return prepared

    async def _reserve_exchange(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        agent: Agent,
        conversation: Conversation,
        request: ChatStreamRequest,
        message: str,
        message_fingerprint: str,
        show_citations: bool,
    ) -> ReservedChat | PreparedChat:
        locked_conversation = await session.scalar(
            select(Conversation)
            .where(
                Conversation.id == conversation.id,
                Conversation.tenant_id == tenant_id,
                Conversation.agent_id == agent.id,
            )
            .with_for_update()
        )
        if not locked_conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if request.idempotency_key:
            user_message = await session.scalar(
                select(Message).where(
                    Message.conversation_id == conversation.id,
                    Message.idempotency_key == request.idempotency_key,
                    Message.role == MessageRole.USER,
                )
            )
            if user_message:
                existing_fingerprint = (
                    user_message.content_fingerprint
                    or hashlib.sha256(user_message.content.encode("utf-8")).hexdigest()
                )
                if existing_fingerprint != message_fingerprint:
                    raise HTTPException(
                        status_code=409,
                        detail="Idempotency key was already used for a different message",
                    )
                assistant = await session.scalar(
                    select(Message).where(
                        Message.conversation_id == conversation.id,
                        Message.sequence == user_message.sequence + 1,
                        Message.role == MessageRole.ASSISTANT,
                    )
                )
                if not assistant:
                    raise HTTPException(
                        status_code=409, detail="An identical message is still being processed"
                    )
                if assistant.finish_reason == "stop":
                    prepared = await self._prepared_from_message(
                        session,
                        conversation.id,
                        assistant,
                        show_citations=show_citations,
                    )
                    await session.commit()
                    return prepared
                processing_started_at = assistant.processing_started_at or assistant.created_at
                if processing_started_at.tzinfo is None:
                    processing_started_at = processing_started_at.replace(tzinfo=UTC)
                lease_expired = processing_started_at < datetime.now(UTC) - timedelta(minutes=5)
                if assistant.finish_reason == "processing" and not lease_expired:
                    raise HTTPException(
                        status_code=409, detail="An identical message is still being processed"
                    )
                lease_id = uuid4().hex
                assistant.content = ""
                assistant.finish_reason = "processing"
                assistant.model_request_id = lease_id
                assistant.processing_started_at = datetime.now(UTC)
                await session.commit()
                return ReservedChat(conversation.id, assistant.id, assistant.sequence, lease_id)

        sequence = int(
            await session.scalar(
                select(func.coalesce(func.max(Message.sequence), 0)).where(
                    Message.conversation_id == conversation.id
                )
            )
            or 0
        )
        user_message = Message(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            sequence=sequence + 1,
            role=MessageRole.USER,
            content=message,
            content_fingerprint=message_fingerprint,
            idempotency_key=request.idempotency_key,
        )
        session.add(user_message)
        locked_conversation.updated_at = datetime.now(UTC)
        lease_id = uuid4().hex
        assistant = Message(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            sequence=sequence + 2,
            role=MessageRole.ASSISTANT,
            content="",
            model_request_id=lease_id,
            finish_reason="processing",
            processing_started_at=datetime.now(UTC),
        )
        session.add(assistant)
        enqueue_event(
            session,
            tenant_id=tenant_id,
            aggregate_type="conversation",
            aggregate_id=conversation.id,
            event_type="chat.message.created.v1",
            payload={"conversationId": str(conversation.id), "agentId": str(agent.id), "role": "user"},
        )
        await session.flush()
        assistant_id = assistant.id
        await session.commit()
        return ReservedChat(conversation.id, assistant_id, assistant.sequence, lease_id)

    async def _recent_history(self, session: AsyncSession, reservation: ReservedChat) -> str:
        rows = (
            await session.scalars(
                select(Message)
                .where(
                    Message.conversation_id == reservation.conversation_id,
                    Message.sequence < reservation.assistant_sequence - 1,
                    or_(
                        Message.role == MessageRole.USER,
                        Message.finish_reason == "stop",
                    ),
                )
                .order_by(Message.sequence.desc())
                .limit(8)
            )
        ).all()
        ordered = list(reversed(rows))
        history = "\n".join(f"{item.role.value}: {item.content}" for item in ordered)
        return history[-6_000:]

    async def _complete_exchange(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        agent: Agent,
        reservation: ReservedChat,
        question: str,
        answer: str,
        evidence: list[Evidence],
        latency_ms: int,
        show_citations: bool,
    ) -> PreparedChat:
        assistant = await session.scalar(
            select(Message)
            .where(
                Message.id == reservation.assistant_message_id,
                Message.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        if not assistant:
            raise HTTPException(status_code=409, detail="Chat request reservation no longer exists")
        if assistant.finish_reason == "stop":
            prepared = await self._prepared_from_message(
                session,
                reservation.conversation_id,
                assistant,
                show_citations=show_citations,
            )
            await session.commit()
            return prepared
        if assistant.model_request_id != reservation.lease_id:
            raise HTTPException(status_code=409, detail="Chat request lease was superseded")
        assistant.content = answer
        assistant.completion_tokens = max(1, len(answer.split()))
        assistant.prompt_tokens = max(
            1, len(question.split()) + sum(len(item.text.split()) for item in evidence)
        )
        assistant.latency_ms = latency_ms
        assistant.confidence = evidence[0].score if evidence else 0.0
        assistant.finish_reason = "stop"
        assistant.processing_started_at = None
        for rank, item in enumerate(evidence, start=1):
            session.add(
                MessageCitation(
                    tenant_id=tenant_id,
                    message_id=assistant.id,
                    source_id=UUID(item.source_id) if item.source_id else None,
                    fact_id=UUID(item.entity_id) if item.kind == "fact" else None,
                    chunk_id=UUID(item.entity_id) if item.kind == "chunk" else None,
                    rank=rank,
                    retrieval_score=item.score,
                    title=item.title,
                    url=item.url,
                )
            )
        enqueue_event(
            session,
            tenant_id=tenant_id,
            aggregate_type="conversation",
            aggregate_id=reservation.conversation_id,
            event_type="chat.response.completed.v1",
            payload={
                "conversationId": str(reservation.conversation_id),
                "agentId": str(agent.id),
                "messageId": str(assistant.id),
                "latencyMs": latency_ms,
                "evidenceCount": len(evidence),
                "answerable": bool(evidence),
                "conversationStarted": assistant.sequence == 2,
            },
        )
        await session.commit()
        return PreparedChat(
            reservation.conversation_id,
            assistant.id,
            answer,
            evidence if show_citations else [],
        )

    async def _get_agent(self, session: AsyncSession, tenant_id: UUID, identifier: UUID | str) -> Agent:
        conditions = [Agent.public_id == str(identifier)]
        try:
            conditions.append(Agent.id == UUID(str(identifier)))
        except ValueError:
            pass
        agent = await session.scalar(
            select(Agent).where(Agent.tenant_id == tenant_id, Agent.deleted_at.is_(None), or_(*conditions))
        )
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        return agent

    async def _get_or_create_conversation(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        agent: Agent,
        request: ChatStreamRequest,
        channel: Channel,
    ) -> Conversation:
        conversation: Conversation | None = None
        idempotent_conversation: Conversation | None = None
        if request.idempotency_key:
            # Serialize the first reservation across processes. The database-level
            # uniqueness constraint remains the final guard; this lock lets a retry
            # discover the winning conversation before it attempts an insert.
            if session.bind and session.bind.dialect.name == "postgresql":
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
                    {"scope": (f"chat:{tenant_id}:{request.idempotency_key}")},
                )
            idempotent_conversation = await session.scalar(
                select(Conversation)
                .join(Message, Message.conversation_id == Conversation.id)
                .where(
                    Message.tenant_id == tenant_id,
                    Message.idempotency_key == request.idempotency_key,
                    Message.role == MessageRole.USER,
                )
            )
            if idempotent_conversation and idempotent_conversation.agent_id != agent.id:
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency key was already used for another agent",
                )
        if request.conversation_id:
            conditions = [Conversation.public_id == str(request.conversation_id)]
            try:
                conditions.append(Conversation.id == UUID(str(request.conversation_id)))
            except ValueError:
                pass
            conversation = await session.scalar(
                select(Conversation).where(
                    Conversation.tenant_id == tenant_id,
                    Conversation.agent_id == agent.id,
                    or_(*conditions),
                )
            )
            if not conversation:
                raise HTTPException(status_code=404, detail="Conversation not found")
            if idempotent_conversation and idempotent_conversation.id != conversation.id:
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency key was already used for another conversation",
                )
        elif idempotent_conversation:
            conversation = idempotent_conversation
        if not conversation:
            conversation = Conversation(
                tenant_id=tenant_id,
                agent_id=agent.id,
                visitor_id=request.visitor_id,
                channel=channel,
                knowledge_revision=agent.published_knowledge_revision,
            )
            session.add(conversation)
            await session.flush()
        return conversation

    async def _prepared_from_message(
        self,
        session: AsyncSession,
        conversation_id: UUID,
        assistant: Message,
        *,
        show_citations: bool,
    ) -> PreparedChat:
        evidence: list[Evidence] = []
        if show_citations:
            citations = (
                await session.scalars(
                    select(MessageCitation)
                    .where(MessageCitation.message_id == assistant.id)
                    .order_by(MessageCitation.rank)
                )
            ).all()
            evidence = [
                Evidence(
                    kind="citation",
                    entity_id=str(citation.id),
                    source_id=str(citation.source_id) if citation.source_id else None,
                    title=citation.title,
                    url=citation.url,
                    text="",
                    score=citation.retrieval_score,
                )
                for citation in citations
            ]
        return PreparedChat(conversation_id, assistant.id, assistant.content, evidence)

    async def _mark_reservation_failed(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        reservation: ReservedChat,
    ) -> None:
        try:
            assistant = await session.scalar(
                select(Message).where(
                    Message.id == reservation.assistant_message_id,
                    Message.tenant_id == tenant_id,
                )
            )
            if assistant and assistant.model_request_id == reservation.lease_id:
                assistant.content = "The assistant could not complete this response."
                assistant.finish_reason = "failed"
                assistant.processing_started_at = None
                await session.commit()
        except Exception:
            await session.rollback()


def _evidence_fallback(evidence: Evidence) -> str:
    if evidence.kind == "fact" and "\nA: " in evidence.text:
        return evidence.text.split("\nA: ", 1)[1].strip()
    # Avoid dumping an entire document into the response.
    sentences = re.split(r"(?<=[.!?])\s+", evidence.text.strip())
    return " ".join(sentences[:3])[:1200]


def _retrieval_query(question: str, conversation_history: str) -> str:
    normalized = question.casefold()
    context_markers = re.compile(
        r"\b(it|its|that|this|those|these|they|them|their|he|she|his|her|former|latter)\b"
        r"|\b(what|how) about\b"
    )
    if not conversation_history or len(question.split()) > 24 or not context_markers.search(normalized):
        return question
    prior_user_messages = [
        line.removeprefix("user: ") for line in conversation_history.splitlines() if line.startswith("user: ")
    ]
    if not prior_user_messages:
        return question
    # Use the fully specified prior question for retrieval; the current follow-up
    # still reaches the generator, where it narrows how the retrieved facts apply.
    # Concatenating pronouns and filler into a websearch query can turn useful
    # lexical terms into an over-constrained AND expression.
    return prior_user_messages[-1]


def token_chunks(value: str) -> list[str]:
    return re.findall(r"\S+\s*", value)


chat_coordinator = ChatCoordinator()
