from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

import orjson
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from northstar_api.database import get_session
from northstar_api.dependencies import WorkspaceWritePrincipal
from northstar_api.models import Channel, Conversation, Message, MessageFeedback
from northstar_api.schemas import ChatStreamRequest, FeedbackCreate, OpenAICompletionRequest
from northstar_api.services.chat import chat_coordinator, token_chunks
from northstar_api.services.outbox import enqueue_event
from northstar_api.services.streaming import chat_sse_response

router = APIRouter(tags=["chat"])
DB = Annotated[AsyncSession, Depends(get_session)]


@router.post("/chat/stream")
async def stream_chat(
    payload: ChatStreamRequest,
    session: DB,
    principal: WorkspaceWritePrincipal,
) -> StreamingResponse:
    prepared = await chat_coordinator.prepare(
        session, tenant_id=principal.tenant_id, request=payload, channel=Channel.WIDGET
    )
    return chat_sse_response(prepared)


@router.post("/messages/{message_id}/feedback", status_code=204)
async def message_feedback(
    message_id: uuid.UUID,
    payload: FeedbackCreate,
    session: DB,
    principal: WorkspaceWritePrincipal,
) -> None:
    message = await session.scalar(
        select(Message).where(Message.id == message_id, Message.tenant_id == principal.tenant_id)
    )
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    agent_id = await session.scalar(
        select(Conversation.agent_id).where(
            Conversation.id == message.conversation_id,
            Conversation.tenant_id == principal.tenant_id,
        )
    )
    if not agent_id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    existing = await session.scalar(
        select(MessageFeedback).where(
            MessageFeedback.message_id == message_id, MessageFeedback.tenant_id == principal.tenant_id
        )
    )
    previous_value = existing.value if existing else None
    if existing:
        existing.value, existing.reason = payload.value, payload.reason
    else:
        session.add(
            MessageFeedback(
                tenant_id=principal.tenant_id,
                message_id=message_id,
                value=payload.value,
                reason=payload.reason,
            )
        )
    enqueue_event(
        session,
        tenant_id=principal.tenant_id,
        aggregate_type="message",
        aggregate_id=message_id,
        event_type="feedback.recorded.v1",
        payload={
            "messageId": str(message_id),
            "agentId": str(agent_id),
            "previousValue": previous_value,
            "value": payload.value,
        },
    )
    await session.commit()


@router.post("/chat/completions")
async def openai_completion(
    payload: OpenAICompletionRequest,
    session: DB,
    principal: WorkspaceWritePrincipal,
) -> Response:
    question = next((item.content for item in reversed(payload.messages) if item.role == "user"), None)
    if not question:
        raise HTTPException(status_code=422, detail="At least one user message is required")
    prepared = await chat_coordinator.prepare(
        session,
        tenant_id=principal.tenant_id,
        request=ChatStreamRequest(agent_id=payload.model, message=question, visitor_id=payload.user),
        channel=Channel.API,
    )
    completion_id = f"chatcmpl-{prepared.assistant_message_id.hex}"
    created = int(time.time())
    if not payload.stream:
        return JSONResponse(
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": payload.model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": prepared.answer},
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    async def chunks() -> AsyncIterator[bytes]:
        for content in token_chunks(prepared.answer):
            body = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": payload.model,
                "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
            }
            yield b"data: " + orjson.dumps(body) + b"\n\n"
            await asyncio.sleep(0)
        done = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": payload.model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield b"data: " + orjson.dumps(done) + b"\n\n"
        yield b"data: [DONE]\n\n"

    return StreamingResponse(chunks(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})
