from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, field_validator, model_validator

from northstar_api.models import (
    AgentStatus,
    AgentTone,
    Channel,
    ConversationState,
    FactStatus,
    JobStatus,
    KnowledgeKind,
    KnowledgeStatus,
    MessageRole,
    Role,
    Sentiment,
)


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(item.capitalize() for item in rest)


class APIModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, from_attributes=True, extra="forbid"
    )


class ErrorDetail(APIModel):
    code: str
    message: str
    request_id: str | None = None


class ErrorResponse(APIModel):
    error: ErrorDetail


class LoginRequest(APIModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    workspace: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[a-z0-9-]+$")


class RefreshRequest(APIModel):
    refresh_token: str | None = None


class LogoutRequest(APIModel):
    refresh_token: str | None = None


class UserOut(APIModel):
    id: UUID
    name: str
    email: EmailStr
    role: Role


class SessionOut(APIModel):
    access_token: str
    expires_at: datetime
    user: UserOut
    refresh_token: str | None = None


class AgentAppearance(APIModel):
    primary_color: str = "#146cf6"
    surface_color: str = "#f6f8fb"
    position: Literal["bottom-right", "bottom-left"] = "bottom-right"
    launcher_style: Literal["spark", "bubble", "avatar"] = "spark"
    welcome_title: str = Field(default="How can I help you?", max_length=120)
    welcome_message: str = Field(
        default="Ask a question and I'll find the most useful answer.", max_length=500
    )
    placeholder: str = Field(default="Ask me anything...", max_length=120)
    suggested_questions: list[str] = Field(default_factory=list, max_length=8)
    show_branding: bool = True

    @field_validator("primary_color", "surface_color")
    @classmethod
    def valid_color(cls, value: str) -> str:
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("must be a six-digit hex color")
        return value.lower()


class AgentModelProfile(APIModel):
    provider: Literal["nvidia"] = "nvidia"
    model: str = "nvidia/nemotron-3-ultra-550b-a55b"
    temperature: float = Field(default=1, ge=0, le=2)
    top_p: float = Field(default=0.95, gt=0, le=1)
    max_tokens: int = Field(default=16_384, ge=64, le=32_768)
    enable_thinking: bool = True
    citation_mode: Literal["always", "when-available", "off"] = "when-available"


class AgentSecurity(APIModel):
    allowed_domains: list[str] = Field(default_factory=lambda: ["localhost"], max_length=100)
    rate_limit_per_minute: int = Field(default=30, ge=1, le=10_000)
    collect_email: bool = False
    mask_sensitive_data: bool = True
    retention_days: int = Field(default=90, ge=1, le=3650)

    @field_validator("allowed_domains")
    @classmethod
    def normalize_domains(cls, values: list[str]) -> list[str]:
        domains: list[str] = []
        for raw in values:
            value = raw.lower().strip().removeprefix("https://").removeprefix("http://").split("/")[0]
            if not value or any(character.isspace() for character in value):
                raise ValueError("invalid allowed domain")
            domains.append(value)
        return sorted(set(domains))


class AgentCreate(APIModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)
    template: str | None = Field(default=None, max_length=80)


class AgentPatch(APIModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    instructions: str | None = Field(default=None, max_length=20_000)
    status: AgentStatus | None = None
    tone: AgentTone | None = None
    language: str | None = Field(default=None, max_length=60)
    avatar: str | None = Field(default=None, max_length=500)
    appearance: AgentAppearance | None = None
    model: AgentModelProfile | None = None
    security: AgentSecurity | None = None


class AgentOut(APIModel):
    id: UUID
    public_id: str
    name: str
    description: str
    instructions: str
    status: AgentStatus
    tone: AgentTone
    language: str
    avatar: str
    conversations: int
    resolution_rate: float
    knowledge_count: int
    last_updated: datetime
    created_at: datetime
    appearance: AgentAppearance
    model: AgentModelProfile
    security: AgentSecurity


class KnowledgeCreate(APIModel):
    name: str = Field(min_length=1, max_length=240)
    kind: KnowledgeKind
    url: HttpUrl | None = None
    content: str | None = Field(default=None, max_length=2_000_000)
    object_key: str | None = Field(default=None, max_length=1000)

    @field_validator("content")
    @classmethod
    def reject_empty_content(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("content cannot be blank")
        return value


class KnowledgeOut(APIModel):
    id: UUID
    agent_id: UUID
    name: str
    kind: KnowledgeKind
    status: KnowledgeStatus
    size_label: str
    chunks: int
    updated_at: datetime
    url: str | None = None
    content: str | None = None
    error: str | None = None


class FactCreate(APIModel):
    question: str = Field(min_length=2, max_length=10_000)
    answer: str = Field(min_length=1, max_length=50_000)
    priority: int = Field(default=0, ge=-100, le=100)
    status: FactStatus = FactStatus.APPROVED


class FactPatch(APIModel):
    question: str | None = Field(default=None, min_length=2, max_length=10_000)
    answer: str | None = Field(default=None, min_length=1, max_length=50_000)
    priority: int | None = Field(default=None, ge=-100, le=100)
    status: FactStatus | None = None


class FactOut(APIModel):
    id: UUID
    source_id: UUID | None
    agent_id: UUID
    question: str
    answer: str
    status: FactStatus
    priority: int
    revision: int
    created_at: datetime
    updated_at: datetime


class JobOut(APIModel):
    id: UUID
    source_id: UUID
    status: JobStatus
    step: str
    progress: int
    attempts: int
    error: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class UploadPresignRequest(APIModel):
    filename: str = Field(min_length=1, max_length=240)
    content_type: Literal[
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
        "text/markdown",
    ]
    size_bytes: int = Field(gt=0)
    checksum_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")

    @model_validator(mode="after")
    def validate_filename_type(self) -> UploadPresignRequest:
        extension = self.filename.lower().rsplit(".", 1)[-1] if "." in self.filename else ""
        expected = {
            "pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "txt": "text/plain",
            "md": "text/markdown",
            "markdown": "text/markdown",
        }.get(extension)
        if not expected:
            raise ValueError("filename must use a supported PDF, DOCX, TXT, or Markdown extension")
        if self.content_type != expected:
            raise ValueError("contentType does not match the filename extension")
        return self


class UploadPresignOut(APIModel):
    method: Literal["POST"] = "POST"
    url: str
    object_key: str
    fields: dict[str, str]
    expires_at: datetime


class CitationOut(APIModel):
    title: str
    url: str | None = None


class MessageOut(APIModel):
    id: UUID
    role: MessageRole
    content: str
    created_at: datetime
    citations: list[CitationOut] = Field(default_factory=list)


class ConversationOut(APIModel):
    id: UUID
    agent_id: UUID
    visitor_name: str
    visitor_email: EmailStr | None = None
    channel: Channel
    state: ConversationState
    sentiment: Sentiment
    preview: str
    unread: int
    started_at: datetime
    updated_at: datetime
    messages: list[MessageOut] = Field(default_factory=list)


class ConversationPatch(APIModel):
    state: ConversationState | None = None
    unread: int | None = Field(default=None, ge=0)
    visitor_name: str | None = Field(default=None, max_length=160)


class ConversationReplyCreate(APIModel):
    content: str = Field(min_length=1, max_length=12_000)


T = TypeVar("T")


class PageResult(APIModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class ChatStreamRequest(APIModel):
    agent_id: UUID | str
    message: str = Field(min_length=1, max_length=12_000)
    conversation_id: UUID | str | None = None
    visitor_id: str | None = Field(default=None, max_length=128)
    idempotency_key: str | None = Field(default=None, max_length=128)


class LeadCreate(APIModel):
    agent_id: UUID
    conversation_id: UUID | None = None
    name: str | None = Field(default=None, max_length=160)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=80)
    consent: bool = False
    fields: dict[str, Any] = Field(default_factory=dict)


class LeadPatch(APIModel):
    status: str = Field(min_length=1, max_length=40)


class LeadOut(APIModel):
    id: UUID
    agent_id: UUID
    conversation_id: UUID | None
    name: str | None
    email: EmailStr | None
    phone: str | None
    status: str
    consent: bool
    fields: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class AnalyticsPoint(APIModel):
    label: str
    conversations: int
    resolved: int


class TopQuestion(APIModel):
    question: str
    count: int
    resolution_rate: float


class ChannelMetric(APIModel):
    channel: str
    value: float


class AnalyticsSummary(APIModel):
    period: str
    conversations: int
    conversations_delta: float
    resolution_rate: float
    resolution_delta: float
    avg_response_seconds: float
    satisfaction: float
    chart: list[AnalyticsPoint]
    top_questions: list[TopQuestion]
    channels: list[ChannelMetric]


class IntegrationOut(APIModel):
    id: str
    name: str
    description: str
    category: Literal["channel", "automation", "data", "developer"]
    icon: str
    connected: bool
    coming_soon: bool = False


class IntegrationPatch(APIModel):
    connected: bool


class WidgetBootstrap(APIModel):
    agent_id: UUID
    public_id: str
    name: str
    avatar: str
    appearance: AgentAppearance
    collect_email: bool
    session_endpoint: str
    stream_endpoint: str


class WidgetSessionCreate(APIModel):
    visitor_id: str | None = Field(default=None, max_length=128)
    visitor_name: str | None = Field(default=None, max_length=160)
    visitor_email: EmailStr | None = None
    page_url: str | None = Field(default=None, max_length=2048)


class WidgetSessionOut(APIModel):
    conversation_id: UUID
    conversation_public_id: str
    session_token: str
    expires_at: datetime


class WidgetMessageRequest(APIModel):
    message: str = Field(min_length=1, max_length=12_000)
    idempotency_key: str | None = Field(default=None, max_length=128)


class FeedbackCreate(APIModel):
    value: Literal[-1, 1]
    reason: str | None = Field(default=None, max_length=500)


class OpenAIMessage(APIModel):
    role: Literal["user", "assistant", "system"]
    content: str = Field(max_length=50_000)


class OpenAICompletionRequest(APIModel):
    model: str
    messages: list[OpenAIMessage] = Field(min_length=1, max_length=100)
    stream: bool = False
    user: str | None = None


class DailyHealthOut(APIModel):
    day: date
    conversations: int
    resolved: int
    escalated: int
    average_response_seconds: float
