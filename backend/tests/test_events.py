from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import orjson
import pytest

from northstar_api.workers.analytics_consumer import _analytics_deltas
from northstar_api.workers.event_utils import EventEnvelope, parse_envelope


def _event(event_type: str, payload: dict[str, object]) -> EventEnvelope:
    return EventEnvelope(
        event_id=uuid4(),
        event_type=event_type,
        tenant_id=uuid4(),
        occurred_at=datetime.now(UTC),
        payload=payload,
    )


def test_chat_projection_counts_conversations_once_and_every_response() -> None:
    first = _analytics_deltas(
        _event(
            "chat.response.completed.v1",
            {"agentId": str(uuid4()), "latencyMs": 275, "conversationStarted": True},
        )
    )
    follow_up = _analytics_deltas(
        _event(
            "chat.response.completed.v1",
            {"agentId": str(uuid4()), "latencyMs": 125, "conversationStarted": False},
        )
    )

    assert (first["conversations"], first["responses"], first["response_ms_total"]) == (1, 1, 275)
    assert (follow_up["conversations"], follow_up["responses"]) == (0, 1)


def test_state_and_feedback_projection_apply_reversible_deltas() -> None:
    resolved = _analytics_deltas(
        _event(
            "conversation.state.changed.v1",
            {"agentId": str(uuid4()), "previousState": "open", "state": "resolved"},
        )
    )
    reopened = _analytics_deltas(
        _event(
            "conversation.state.changed.v1",
            {"agentId": str(uuid4()), "previousState": "resolved", "state": "open"},
        )
    )
    positive = _analytics_deltas(
        _event(
            "feedback.recorded.v1",
            {"agentId": str(uuid4()), "previousValue": None, "value": 1},
        )
    )
    changed = _analytics_deltas(
        _event(
            "feedback.recorded.v1",
            {"agentId": str(uuid4()), "previousValue": 1, "value": -1},
        )
    )

    assert resolved["resolved"] == 1
    assert reopened["resolved"] == -1
    assert positive["positive_feedback"] == 1
    assert changed["positive_feedback"] == -1
    assert changed["negative_feedback"] == 1


def test_event_envelope_validation_rejects_malformed_input() -> None:
    valid = {
        "schemaVersion": 1,
        "eventId": str(uuid4()),
        "eventType": "chat.response.completed.v1",
        "tenantId": str(uuid4()),
        "occurredAt": datetime.now(UTC).isoformat(),
        "payload": {"agentId": str(uuid4())},
    }
    parsed = parse_envelope(orjson.dumps(valid))
    assert parsed.event_type == valid["eventType"]

    for invalid in (
        b"not-json",
        orjson.dumps({**valid, "schemaVersion": 2}),
        orjson.dumps({**valid, "tenantId": "not-a-uuid"}),
        orjson.dumps({**valid, "payload": []}),
    ):
        with pytest.raises(ValueError, match="validation failed"):
            parse_envelope(invalid)
