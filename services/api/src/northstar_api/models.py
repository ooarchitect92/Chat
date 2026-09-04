from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from northstar_api.config import get_settings
from northstar_api.database import Base

JSONType = JSON().with_variant(JSONB, "postgresql")
MutableJSON = MutableDict.as_mutable(JSONType)
EMBEDDING_DIMENSION = get_settings().embedding_dimension


@compiles(HALFVEC, "sqlite")
def compile_halfvec_sqlite(_: HALFVEC, __: Any, **___: Any) -> str:
    return "JSON"


def enum_column(enum_type: type[enum.Enum], name: str) -> SAEnum:
    return SAEnum(
        enum_type,
        name=name,
        native_enum=False,
        values_callable=lambda values: [item.value for item in values],
    )


class Role(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    ANALYST = "analyst"


class AgentStatus(str, enum.Enum):
    ACTIVE = "active"
    DRAFT = "draft"
    TRAINING = "training"
    ERROR = "error"


class AgentTone(str, enum.Enum):
    PROFESSIONAL = "professional"
    FRIENDLY = "friendly"
    CONCISE = "concise"
    EMPATHETIC = "empathetic"
    PLAYFUL = "playful"


class KnowledgeKind(str, enum.Enum):
    FILE = "file"
    URL = "url"
    TEXT = "text"
    SITEMAP = "sitemap"


class KnowledgeStatus(str, enum.Enum):
    READY = "ready"
    PROCESSING = "processing"
    FAILED = "failed"


class FactStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class ConversationState(str, enum.Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


class Sentiment(str, enum.Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class Channel(str, enum.Enum):
    WIDGET = "widget"
    API = "api"
    SLACK = "slack"
    WHATSAPP = "whatsapp"


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    AGENT = "agent"
    SYSTEM = "system"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class Tenant(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "tenants"

    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    plan: Mapped[str] = mapped_column(String(40), default="starter")
    settings_json: Mapped[dict[str, Any]] = mapped_column(MutableJSON, default=dict)

    memberships: Mapped[list[TenantMembership]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    agents: Mapped[list[Agent]] = relationship(back_populates="tenant", cascade="all, delete-orphan")


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    password_hash: Mapped[str] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list[TenantMembership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class TenantMembership(Base, TimestampMixin):
    __tablename__ = "tenant_memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[Role] = mapped_column(enum_column(Role, "membership_role"), default=Role.MEMBER)

    tenant: Mapped[Tenant] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


def default_appearance() -> dict[str, Any]:
    return {
        "primaryColor": "#146cf6",
        "surfaceColor": "#f6f8fb",
        "position": "bottom-right",
        "launcherStyle": "spark",
        "welcomeTitle": "How can I help you?",
        "welcomeMessage": "Ask a question and I'll find the most useful answer.",
        "placeholder": "Ask me anything...",
        "suggestedQuestions": [
            "What services do you offer?",
            "How can I contact support?",
            "Tell me about your plans.",
        ],
        "showBranding": True,
    }


def default_model_profile() -> dict[str, Any]:
    settings = get_settings()
    return {
        "provider": "nvidia",
        "model": settings.nvidia_model,
        "temperature": settings.nvidia_temperature,
        "topP": settings.nvidia_top_p,
        "maxTokens": settings.nvidia_max_tokens,
        "enableThinking": settings.nvidia_enable_thinking,
        "citationMode": "when-available",
    }


def default_security() -> dict[str, Any]:
    return {
        "allowedDomains": ["localhost"],
        "rateLimitPerMinute": get_settings().default_rate_limit_per_minute,
        "collectEmail": False,
        "maskSensitiveData": True,
        "retentionDays": 90,
    }


class Agent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name"),
        Index("ix_agents_tenant_status", "tenant_id", "status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    public_id: Mapped[str] = mapped_column(String(48), unique=True, default=lambda: f"agt_{uuid.uuid4().hex}")
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(500), default="")
    instructions: Mapped[str] = mapped_column(
        Text, default="Answer accurately using only approved knowledge."
    )
    status: Mapped[AgentStatus] = mapped_column(
        enum_column(AgentStatus, "agent_status"), default=AgentStatus.DRAFT
    )
    tone: Mapped[AgentTone] = mapped_column(enum_column(AgentTone, "agent_tone"), default=AgentTone.FRIENDLY)
    language: Mapped[str] = mapped_column(String(60), default="English")
    avatar: Mapped[str] = mapped_column(String(500), default="N")
    appearance: Mapped[dict[str, Any]] = mapped_column(MutableJSON, default=default_appearance)
    model_profile: Mapped[dict[str, Any]] = mapped_column(MutableJSON, default=default_model_profile)
    security: Mapped[dict[str, Any]] = mapped_column(MutableJSON, default=default_security)
    published_knowledge_revision: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tenant: Mapped[Tenant] = relationship(back_populates="agents")
    knowledge_sources: Mapped[list[KnowledgeSource]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    conversations: Mapped[list[Conversation]] = relationship(back_populates="agent")


class KnowledgeSource(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "knowledge_sources"
    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", "name"),
        Index("ix_knowledge_sources_agent_status", "tenant_id", "agent_id", "status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(240))
    kind: Mapped[KnowledgeKind] = mapped_column(enum_column(KnowledgeKind, "knowledge_kind"))
    status: Mapped[KnowledgeStatus] = mapped_column(
        enum_column(KnowledgeStatus, "knowledge_status"), default=KnowledgeStatus.PROCESSING
    )
    size_label: Mapped[str] = mapped_column(String(80), default="Queued")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    url: Mapped[str | None] = mapped_column(String(2048))
    content: Mapped[str | None] = mapped_column(Text)
    object_key: Mapped[str | None] = mapped_column(String(1000))
    checksum: Mapped[str | None] = mapped_column(String(64), index=True)
    error: Mapped[str | None] = mapped_column(String(1000))
    revision: Mapped[int] = mapped_column(Integer, default=1)

    agent: Mapped[Agent] = relationship(back_populates="knowledge_sources")
    chunks: Mapped[list[DocumentChunk]] = relationship(back_populates="source", cascade="all, delete-orphan")
    facts: Mapped[list[KnowledgeFact]] = relationship(back_populates="source", cascade="all, delete-orphan")


class DocumentChunk(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("source_id", "ordinal", "revision"),
        Index("ix_document_chunks_scope", "tenant_id", "agent_id", "approved", "revision"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    approved: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    embedding: Mapped[list[float] | None] = mapped_column(HALFVEC(EMBEDDING_DIMENSION))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(MutableJSON, default=dict)

    source: Mapped[KnowledgeSource] = relationship(back_populates="chunks")


class KnowledgeFact(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "knowledge_facts"
    __table_args__ = (Index("ix_knowledge_facts_scope", "tenant_id", "agent_id", "status", "revision"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="SET NULL"), index=True
    )
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    status: Mapped[FactStatus] = mapped_column(
        enum_column(FactStatus, "fact_status"), default=FactStatus.PENDING
    )
    priority: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    embedding: Mapped[list[float] | None] = mapped_column(HALFVEC(EMBEDDING_DIMENSION))
    source_span: Mapped[dict[str, Any]] = mapped_column(MutableJSON, default=dict)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source: Mapped[KnowledgeSource | None] = relationship(back_populates="facts")


class IngestionJob(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ingestion_jobs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[JobStatus] = mapped_column(enum_column(JobStatus, "job_status"), default=JobStatus.QUEUED)
    step: Mapped[str] = mapped_column(String(80), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    error_json: Mapped[dict[str, Any]] = mapped_column(MutableJSON, default=dict)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    dispatch_attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_dispatch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Conversation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_scope_updated", "tenant_id", "agent_id", "updated_at"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="RESTRICT"), index=True)
    public_id: Mapped[str] = mapped_column(String(48), unique=True, default=lambda: f"cnv_{uuid.uuid4().hex}")
    visitor_id: Mapped[str | None] = mapped_column(String(128), index=True)
    visitor_name: Mapped[str] = mapped_column(String(160), default="Anonymous visitor")
    visitor_email: Mapped[str | None] = mapped_column(String(320))
    channel: Mapped[Channel] = mapped_column(
        enum_column(Channel, "conversation_channel"), default=Channel.WIDGET
    )
    state: Mapped[ConversationState] = mapped_column(
        enum_column(ConversationState, "conversation_state"), default=ConversationState.OPEN
    )
    sentiment: Mapped[Sentiment] = mapped_column(
        enum_column(Sentiment, "sentiment"), default=Sentiment.NEUTRAL
    )
    unread_count: Mapped[int] = mapped_column(Integer, default=0)
    knowledge_revision: Mapped[int] = mapped_column(Integer, default=1)
    page_url: Mapped[str | None] = mapped_column(String(2048))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(String(120))

    agent: Mapped[Agent] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.sequence"
    )


class Message(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "sequence",
            name="uq_messages_conversation_sequence",
        ),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_messages_tenant_idempotency",
        ),
        Index("ix_messages_tenant_created", "tenant_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[MessageRole] = mapped_column(enum_column(MessageRole, "message_role"))
    content: Mapped[str] = mapped_column(Text)
    content_fingerprint: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    model_request_id: Mapped[str | None] = mapped_column(String(200))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float | None] = mapped_column(Float)
    finish_reason: Mapped[str | None] = mapped_column(String(80))
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    citations: Mapped[list[MessageCitation]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class MessageCitation(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "message_citations"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="SET NULL")
    )
    fact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("knowledge_facts.id", ondelete="SET NULL"))
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_chunks.id", ondelete="SET NULL"))
    rank: Mapped[int] = mapped_column(Integer)
    retrieval_score: Mapped[float] = mapped_column(Float)
    title: Mapped[str] = mapped_column(String(240))
    url: Mapped[str | None] = mapped_column(String(2048))

    message: Mapped[Message] = relationship(back_populates="citations")


class MessageFeedback(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "message_feedback"
    __table_args__ = (UniqueConstraint("tenant_id", "message_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    value: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String(500))


class Lead(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "leads"
    __table_args__ = (Index("ix_leads_scope_created", "tenant_id", "agent_id", "created_at"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str | None] = mapped_column(String(160))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), default="new")
    consent: Mapped[bool] = mapped_column(Boolean, default=False)
    fields_json: Mapped[dict[str, Any]] = mapped_column(MutableJSON, default=dict)


class IntegrationConnection(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "integration_connections"
    __table_args__ = (UniqueConstraint("tenant_id", "integration_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    integration_id: Mapped[str] = mapped_column(String(80))
    connected: Mapped[bool] = mapped_column(Boolean, default=False)
    config_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)


class AgentHealthDaily(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "agent_health_daily"
    __table_args__ = (UniqueConstraint("tenant_id", "agent_id", "day"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date)
    conversations: Mapped[int] = mapped_column(Integer, default=0)
    responses: Mapped[int] = mapped_column(Integer, default=0)
    resolved: Mapped[int] = mapped_column(Integer, default=0)
    escalated: Mapped[int] = mapped_column(Integer, default=0)
    response_ms_total: Mapped[int] = mapped_column(Integer, default=0)
    positive_feedback: Mapped[int] = mapped_column(Integer, default=0)
    negative_feedback: Mapped[int] = mapped_column(Integer, default=0)


class OutboxEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_unpublished", "published_at", "occurred_at"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(80))
    aggregate_id: Mapped[str] = mapped_column(String(80))
    event_type: Mapped[str] = mapped_column(String(160))
    payload: Mapped[dict[str, Any]] = mapped_column(MutableJSON)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500))


class ProcessedEvent(Base):
    __tablename__ = "processed_events"

    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    consumer: Mapped[str] = mapped_column(String(120), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EventQuarantine(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "event_quarantine"
    __table_args__ = (UniqueConstraint("consumer", "topic", "partition", "offset"),)

    consumer: Mapped[str] = mapped_column(String(120))
    topic: Mapped[str] = mapped_column(String(200))
    partition: Mapped[int] = mapped_column(Integer)
    offset: Mapped[int] = mapped_column(Integer)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    payload_excerpt: Mapped[str] = mapped_column(Text)
    error: Mapped[str] = mapped_column(String(500))
    quarantined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
