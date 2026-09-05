from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import orjson
from fastapi.responses import StreamingResponse

from northstar_api.services.chat import PreparedChat, token_chunks


def _frame(payload: dict[str, object] | str) -> bytes:
    data = payload if isinstance(payload, str) else orjson.dumps(payload).decode()
    return f"data: {data}\n\n".encode()


def chat_sse_response(prepared: PreparedChat) -> StreamingResponse:
    async def events() -> AsyncIterator[bytes]:
        yield _frame(
            {
                "type": "start",
                "conversationId": str(prepared.conversation_id),
                "messageId": str(prepared.assistant_message_id),
            }
        )
        for token in token_chunks(prepared.answer):
            yield _frame({"type": "token", "content": token})
            await asyncio.sleep(0)
        seen: set[tuple[str, str | None]] = set()
        for evidence in prepared.evidence:
            key = (evidence.title, evidence.url)
            if key in seen:
                continue
            seen.add(key)
            yield _frame({"type": "citation", "title": evidence.title, "url": evidence.url})
        yield _frame({"type": "done", "conversationId": str(prepared.conversation_id)})
        yield _frame("[DONE]")

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
