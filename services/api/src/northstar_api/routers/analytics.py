from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from northstar_api.database import get_session
from northstar_api.dependencies import CurrentPrincipal
from northstar_api.models import (
    Conversation,
    ConversationState,
    Message,
    MessageFeedback,
    MessageRole,
)
from northstar_api.schemas import AnalyticsPoint, AnalyticsSummary, ChannelMetric, TopQuestion

router = APIRouter(prefix="/analytics", tags=["analytics"])
DB = Annotated[AsyncSession, Depends(get_session)]


@router.get("/summary", response_model=AnalyticsSummary)
async def analytics_summary(principal: CurrentPrincipal, session: DB) -> AnalyticsSummary:
    now = datetime.now(UTC)
    current_start = now - timedelta(days=30)
    previous_start = current_start - timedelta(days=30)
    scope = Conversation.tenant_id == principal.tenant_id

    current_count = int(
        await session.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(scope, Conversation.started_at >= current_start)
        )
        or 0
    )
    previous_count = int(
        await session.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(scope, Conversation.started_at >= previous_start, Conversation.started_at < current_start)
        )
        or 0
    )
    resolved = int(
        await session.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(
                scope,
                Conversation.started_at >= current_start,
                Conversation.state == ConversationState.RESOLVED,
            )
        )
        or 0
    )
    previous_resolved = int(
        await session.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(
                scope,
                Conversation.started_at >= previous_start,
                Conversation.started_at < current_start,
                Conversation.state == ConversationState.RESOLVED,
            )
        )
        or 0
    )
    response_ms = float(
        await session.scalar(
            select(func.avg(Message.latency_ms))
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Conversation.tenant_id == principal.tenant_id,
                Message.role == MessageRole.ASSISTANT,
                Message.created_at >= current_start,
            )
        )
        or 0
    )
    feedback = (
        await session.execute(
            select(MessageFeedback.value, func.count())
            .where(
                MessageFeedback.tenant_id == principal.tenant_id, MessageFeedback.created_at >= current_start
            )
            .group_by(MessageFeedback.value)
        )
    ).all()
    feedback_counts = {int(value): int(count) for value, count in feedback}
    feedback_total = sum(feedback_counts.values())
    satisfaction = round(5 * feedback_counts.get(1, 0) / feedback_total, 1) if feedback_total else 0.0

    conversations = (
        await session.scalars(
            select(Conversation)
            .where(scope, Conversation.started_at >= current_start)
            .order_by(Conversation.started_at)
        )
    ).all()
    chart: list[AnalyticsPoint] = []
    for offset in range(0, 30, 4):
        start = current_start + timedelta(days=offset)
        end = min(start + timedelta(days=4), now + timedelta(seconds=1))
        bucket = []
        for item in conversations:
            started_at = item.started_at if item.started_at.tzinfo else item.started_at.replace(tzinfo=UTC)
            if start <= started_at < end:
                bucket.append(item)
        chart.append(
            AnalyticsPoint(
                label=start.strftime("%b %d"),
                conversations=len(bucket),
                resolved=sum(item.state == ConversationState.RESOLVED for item in bucket),
            )
        )

    top_rows = (
        await session.execute(
            select(
                Message.content,
                func.count(Message.id).label("count"),
                func.sum(case((Conversation.state == ConversationState.RESOLVED, 1), else_=0)).label(
                    "resolved"
                ),
            )
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Conversation.tenant_id == principal.tenant_id,
                Message.role == MessageRole.USER,
                Message.created_at >= current_start,
            )
            .group_by(Message.content)
            .order_by(func.count(Message.id).desc())
            .limit(5)
        )
    ).all()
    top_questions = [
        TopQuestion(
            question=question[:300],
            count=int(count),
            resolution_rate=round(100 * int(row_resolved or 0) / int(count), 1),
        )
        for question, count, row_resolved in top_rows
    ]

    channel_counts = Counter(item.channel.value for item in conversations)
    channels = [
        ChannelMetric(channel=channel.replace("_", " ").title(), value=round(100 * count / current_count, 1))
        for channel, count in channel_counts.most_common()
    ]
    current_resolution = 100 * resolved / current_count if current_count else 0
    previous_resolution = 100 * previous_resolved / previous_count if previous_count else 0
    conversation_delta = 100 * (current_count - previous_count) / previous_count if previous_count else 0
    return AnalyticsSummary(
        period="Last 30 days",
        conversations=current_count,
        conversations_delta=round(conversation_delta, 1),
        resolution_rate=round(current_resolution, 1),
        resolution_delta=round(current_resolution - previous_resolution, 1),
        avg_response_seconds=round(response_ms / 1000, 2),
        satisfaction=satisfaction,
        chart=chart,
        top_questions=top_questions,
        channels=channels,
    )
