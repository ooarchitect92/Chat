from __future__ import annotations

import json
from unittest.mock import AsyncMock
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import select

from northstar_api.database import SessionFactory
from northstar_api.models import Agent, Role, Tenant, TenantMembership, User
from northstar_api.security import hash_password
from northstar_api.services.rate_limit import RateLimitResult, redis_services


async def test_health_and_auth_contract(client: AsyncClient, session_payload: dict) -> None:
    live = await client.get("/health/live")
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert session_payload["user"]["email"] == "owner@example.com"
    assert session_payload["user"]["role"] == "owner"
    assert session_payload["accessToken"]
    assert session_payload["refreshToken"]
    assert session_payload["expiresAt"]
    cookie = client.cookies.get("northstar_refresh")
    assert cookie == session_payload["refreshToken"]

    invalid = await client.post(
        "/api/v1/auth/login", json={"email": "owner@example.com", "password": "definitely-wrong"}
    )
    assert invalid.status_code == 401
    assert invalid.json()["error"]["code"] == "http_401"


async def test_refresh_rotation_rejects_replay(client: AsyncClient, session_payload: dict) -> None:
    original = session_payload["refreshToken"]
    refreshed = await client.post("/api/v1/auth/refresh", json={"refreshToken": original})
    assert refreshed.status_code == 200, refreshed.text
    rotated = refreshed.json()["refreshToken"]
    assert rotated and rotated != original

    replay = await client.post("/api/v1/auth/refresh", json={"refreshToken": original})
    assert replay.status_code == 401
    # Reuse invalidates the current token in that family as a theft precaution.
    revoked_family = await client.post("/api/v1/auth/refresh", json={"refreshToken": rotated})
    assert revoked_family.status_code == 401


async def test_refresh_cookie_rotates_without_a_javascript_token(client: AsyncClient) -> None:
    signed_in = await client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "Correct-Horse-Test-Password-42!"},
    )
    assert signed_in.status_code == 200
    assert signed_in.headers["cache-control"] == "no-store"
    assert "HttpOnly" in signed_in.headers["set-cookie"]
    assert "SameSite=strict" in signed_in.headers["set-cookie"]

    refreshed = await client.post("/api/v1/auth/refresh")

    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.headers["cache-control"] == "no-store"
    assert refreshed.json()["accessToken"]
    assert client.cookies.get("northstar_refresh") == refreshed.json()["refreshToken"]


async def test_login_rate_limit_is_generic_and_sets_retry_after(client: AsyncClient, monkeypatch) -> None:
    limiter = AsyncMock(side_effect=[RateLimitResult(True, 100, 41), RateLimitResult(False, 0, 41)])
    monkeypatch.setattr(redis_services, "check_rate_limit", limiter)

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "unknown@example.com", "password": "not-the-password"},
    )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "41"
    assert response.json()["error"]["message"] == "Too many sign-in attempts. Try again later."
    assert "unknown@example.com" not in response.text
    assert limiter.await_count == 2


async def test_agent_knowledge_and_grounded_sse(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    created = await client.post(
        "/api/v1/agents",
        headers=auth_headers,
        json={"name": "Support Test Agent", "description": "A grounded test agent"},
    )
    assert created.status_code == 201, created.text
    agent = created.json()
    assert agent["model"] == {
        "provider": "nvidia",
        "model": "nvidia/nemotron-3-ultra-550b-a55b",
        "temperature": 1.0,
        "topP": 0.95,
        "maxTokens": 16384,
        "enableThinking": True,
        "citationMode": "when-available",
    }

    updated = await client.patch(
        f"/api/v1/agents/{agent['id']}",
        headers=auth_headers,
        json={"status": "active", "security": {**agent["security"], "allowedDomains": ["testserver"]}},
    )
    assert updated.status_code == 200, updated.text

    knowledge = await client.post(
        f"/api/v1/agents/{agent['id']}/knowledge",
        headers=auth_headers,
        json={
            "name": "Refund policy",
            "kind": "text",
            "content": "Customers may request a full refund within 30 days of the original purchase.",
        },
    )
    assert knowledge.status_code == 201, knowledge.text
    assert knowledge.json()["status"] == "ready"
    assert knowledge.json()["chunks"] == 1

    anonymous_chat = await client.post(
        "/api/v1/chat/stream",
        json={"agentId": agent["id"], "message": "What is the refund policy?"},
    )
    assert anonymous_chat.status_code == 401

    async with client.stream(
        "POST",
        "/api/v1/chat/stream",
        headers=auth_headers,
        json={
            "agentId": agent["id"],
            "message": "What is the refund policy?",
            "visitorId": "test-user",
            "idempotencyKey": "refund-question-1",
        },
    ) as response:
        assert response.status_code == 200, await response.aread()
        body = b"".join([chunk async for chunk in response.aiter_bytes()]).decode()
    payloads = [
        json.loads(line[6:])
        for line in body.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    assert payloads[0]["type"] == "start"
    answer = "".join(item["content"] for item in payloads if item["type"] == "token")
    assert "30 days" in answer
    assert any(item["type"] == "citation" and item["title"] == "Refund policy" for item in payloads)
    assert payloads[-1]["type"] == "done"

    first_start = payloads[0]
    async with client.stream(
        "POST",
        "/api/v1/chat/stream",
        headers=auth_headers,
        json={
            "agentId": agent["id"],
            "message": "What is the refund policy?",
            "visitorId": "test-user",
            "idempotencyKey": "refund-question-1",
        },
    ) as response:
        assert response.status_code == 200, await response.aread()
        replay_body = b"".join([chunk async for chunk in response.aiter_bytes()]).decode()
    replay_payloads = [
        json.loads(line[6:])
        for line in replay_body.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    assert replay_payloads[0]["messageId"] == first_start["messageId"]
    assert any(item["type"] == "citation" for item in replay_payloads)

    async with client.stream(
        "POST",
        "/api/v1/chat/stream",
        headers=auth_headers,
        json={
            "agentId": agent["id"],
            "conversationId": first_start["conversationId"],
            "message": "How long is that?",
            "visitorId": "test-user",
            "idempotencyKey": "refund-follow-up-1",
        },
    ) as response:
        assert response.status_code == 200, await response.aread()
        follow_up_body = b"".join([chunk async for chunk in response.aiter_bytes()]).decode()
    follow_up_payloads = [
        json.loads(line[6:])
        for line in follow_up_body.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    follow_up_answer = "".join(item["content"] for item in follow_up_payloads if item["type"] == "token")
    assert "30 days" in follow_up_answer

    teammate_reply = await client.post(
        f"/api/v1/conversations/{first_start['conversationId']}/messages",
        headers=auth_headers,
        json={"content": "A teammate can also help with this request."},
    )
    assert teammate_reply.status_code == 201, teammate_reply.text
    assert teammate_reply.json()["role"] == "agent"
    assert teammate_reply.json()["content"] == "A teammate can also help with this request."

    idempotency_mismatch = await client.post(
        "/api/v1/chat/stream",
        headers=auth_headers,
        json={
            "agentId": agent["id"],
            "message": "A different question",
            "visitorId": "test-user",
            "idempotencyKey": "refund-question-1",
        },
    )
    assert idempotency_mismatch.status_code == 409

    async with client.stream(
        "POST",
        "/api/v1/chat/stream",
        headers=auth_headers,
        json={
            "agentId": agent["id"],
            "message": "Explain stellar nucleosynthesis in a neutron-star merger.",
            "visitorId": "test-user-unrelated",
        },
    ) as response:
        assert response.status_code == 200, await response.aread()
        unrelated_body = b"".join([chunk async for chunk in response.aiter_bytes()]).decode()
    unrelated_payloads = [
        json.loads(line[6:])
        for line in unrelated_body.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    unrelated_answer = "".join(item["content"] for item in unrelated_payloads if item["type"] == "token")
    assert unrelated_answer == "I don't have enough verified information to answer that."
    assert not any(item["type"] == "citation" for item in unrelated_payloads)

    origin_headers = {"Origin": "http://testserver"}
    bootstrap = await client.get(
        f"/api/v1/widget/{agent['publicId']}/bootstrap",
        headers=origin_headers,
    )
    assert bootstrap.status_code == 200, bootstrap.text
    assert bootstrap.json()["name"] == "Support Test Agent"
    assert bootstrap.headers["access-control-allow-origin"] == "http://testserver"
    forbidden = await client.get(
        f"/api/v1/widget/{agent['publicId']}/bootstrap",
        headers={"Origin": "https://not-allowed.example"},
    )
    assert forbidden.status_code == 403
    spoofed = await client.get(
        f"/api/v1/widget/{agent['publicId']}/bootstrap",
        headers={
            "Origin": "https://not-allowed.example",
            "X-Widget-Origin": "http://testserver",
        },
    )
    assert spoofed.status_code == 403

    preflight = await client.options(
        f"/api/v1/widget/{agent['publicId']}/sessions",
        headers={
            "Origin": "http://testserver",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code == 204
    assert preflight.headers["access-control-allow-origin"] == "http://testserver"

    widget_session = await client.post(
        f"/api/v1/widget/{agent['publicId']}/sessions",
        headers=origin_headers,
        json={"visitorId": "widget-test", "pageUrl": "http://testserver/pricing"},
    )
    assert widget_session.status_code == 201, widget_session.text
    assert widget_session.headers["access-control-allow-origin"] == "http://testserver"
    widget_session_payload = widget_session.json()
    async with client.stream(
        "POST",
        f"/api/v1/widget/sessions/{widget_session_payload['conversationId']}/messages",
        headers={
            "Authorization": f"Bearer {widget_session_payload['sessionToken']}",
        },
        json={"message": "How long is the refund window?", "idempotencyKey": "widget-test-1"},
    ) as response:
        assert response.status_code == 200, await response.aread()
        widget_body = b"".join([chunk async for chunk in response.aiter_bytes()]).decode()
    widget_payloads = [
        json.loads(line[6:])
        for line in widget_body.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    widget_answer = "".join(item["content"] for item in widget_payloads if item["type"] == "token")
    assert "30 days" in widget_answer
    assert any(item["type"] == "citation" and item["title"] == "Refund policy" for item in widget_payloads)

    hosted_bootstrap = await client.get(f"/api/v1/widget/{agent['publicId']}/hosted/bootstrap")
    assert hosted_bootstrap.status_code == 200, hosted_bootstrap.text
    assert hosted_bootstrap.json()["sessionEndpoint"].endswith("/hosted/sessions")
    hosted_session = await client.post(
        f"/api/v1/widget/{agent['publicId']}/hosted/sessions",
        json={"visitorId": "hosted-test"},
    )
    assert hosted_session.status_code == 201, hosted_session.text
    assert hosted_session.json()["sessionToken"]

    conversations = await client.get("/api/v1/conversations", headers=auth_headers)
    assert conversations.status_code == 200
    data = conversations.json()
    assert {"items", "total", "page", "pageSize"} <= data.keys()
    assert data["total"] >= 1


async def test_v1_alias_and_tenant_scoping(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    response = await client.get("/v1/agents", headers=auth_headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)

    async with SessionFactory() as database:
        other_tenant = Tenant(slug="isolated-workspace", name="Isolated Workspace")
        database.add(other_tenant)
        await database.flush()
        other_agent = Agent(tenant_id=other_tenant.id, name="Private Other-Tenant Agent")
        database.add(other_agent)
        await database.commit()
        other_agent_id = other_agent.id

    missing = await client.get(f"/api/v1/agents/{other_agent_id}", headers=auth_headers)
    assert missing.status_code == 404


async def test_analytics_empty_safe_shape(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    response = await client.get("/api/v1/analytics/summary", headers=auth_headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["period"] == "Last 30 days"
    assert isinstance(payload["chart"], list)
    assert isinstance(payload["channels"], list)


async def test_analyst_role_is_read_only(client: AsyncClient) -> None:
    async with SessionFactory() as database:
        tenant = await database.scalar(select(Tenant).where(Tenant.slug == "test-workspace"))
        assert tenant is not None
        analyst = User(
            email="analyst@example.com",
            name="Read Only Analyst",
            password_hash=hash_password("Analyst-Read-Only-Password-42!"),
        )
        database.add(analyst)
        await database.flush()
        database.add(TenantMembership(tenant_id=tenant.id, user_id=analyst.id, role=Role.ANALYST))
        await database.commit()

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "analyst@example.com", "password": "Analyst-Read-Only-Password-42!"},
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['accessToken']}"}

    readable = await client.get("/api/v1/conversations", headers=headers)
    forbidden = await client.patch(
        f"/api/v1/conversations/{uuid4()}",
        headers=headers,
        json={"state": "resolved"},
    )

    assert readable.status_code == 200
    assert forbidden.status_code == 403
