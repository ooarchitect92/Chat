from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import delete, select

from northstar_api.config import Settings
from northstar_api.database import SessionFactory
from northstar_api.models import (
    Agent,
    Channel,
    Conversation,
    ConversationState,
    IntegrationConnection,
    Message,
    Role,
    Tenant,
    WhatsAppConnection,
    WhatsAppInboundMessage,
    WhatsAppOutboundDelivery,
)
from northstar_api.routers import integrations as integrations_router
from northstar_api.routers import whatsapp as webhook_router
from northstar_api.routers.integrations import (
    _consume_whatsapp_signup_session,
    _token_is_current,
    serialize_whatsapp_setup,
)
from northstar_api.routers.whatsapp import InboundEnvelope, _durably_receive
from northstar_api.schemas import WhatsAppCompleteRequest
from northstar_api.security import Principal, create_whatsapp_signup_token, decode_token
from northstar_api.services.chat import PreparedChat, chat_coordinator
from northstar_api.services.rate_limit import redis_services as shared_redis_services
from northstar_api.services.whatsapp import (
    MAX_WHATSAPP_TEXT_LENGTH,
    MetaTokenCipher,
    WhatsAppCloudClient,
    WhatsAppGraphError,
    WhatsAppSetup,
    split_whatsapp_text,
)
from northstar_api.services.whatsapp import (
    token_cipher as shared_token_cipher,
)
from northstar_api.services.whatsapp import (
    whatsapp_client as shared_whatsapp_client,
)
from northstar_api.workers.job_dispatcher import whatsapp_dispatch_failure_state
from northstar_api.workers.tasks import (
    _process_whatsapp_inbound_async,
    _send_whatsapp_human_reply_async,
    whatsapp_thread_lock_key,
)
from northstar_api.workers.tasks import process_whatsapp_inbound as inbound_task
from northstar_api.workers.tasks import send_whatsapp_human_reply as outbound_task


def whatsapp_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "test",
        "meta_app_id": "123456789",
        "meta_app_secret": "meta-app-secret-used-only-in-tests",
        "meta_whatsapp_configuration_id": "987654321",
        "meta_whatsapp_webhook_verify_token": "webhook-verify-token-used-only-in-tests",
        "meta_whatsapp_token_encryption_key": "encryption-key-used-only-in-tests-123456789",
        "meta_graph_api_version": "v26.0",
    }
    values.update(overrides)
    return Settings(**values)


def test_token_cipher_round_trip_and_tamper_detection() -> None:
    cipher = MetaTokenCipher(whatsapp_settings())
    encrypted = cipher.encrypt("EA-test-access-token")

    assert encrypted != b"EA-test-access-token"
    assert cipher.decrypt(encrypted) == "EA-test-access-token"
    with pytest.raises(RuntimeError, match="unreadable"):
        cipher.decrypt(encrypted[:-1] + bytes([encrypted[-1] ^ 1]))


def test_whatsapp_text_is_split_without_exceeding_provider_limit() -> None:
    value = ("A" * 3000) + " " + ("B" * 3000) + "\n" + ("C" * 3000)

    chunks = split_whatsapp_text(value)

    assert "".join(chunks).replace(" ", "").replace("\n", "") == value.replace(" ", "").replace("\n", "")
    assert all(0 < len(chunk) <= MAX_WHATSAPP_TEXT_LENGTH for chunk in chunks)


def test_whatsapp_thread_lock_serializes_a_recipient_across_message_types() -> None:
    connection_id = UUID("00000000-0000-0000-0000-000000000123")

    inbound_key = whatsapp_thread_lock_key(connection_id, "15550101")
    human_reply_key = whatsapp_thread_lock_key(connection_id, "15550101")
    another_customer_key = whatsapp_thread_lock_key(connection_id, "15550102")

    assert inbound_key == human_reply_key
    assert inbound_key != another_customer_key


def test_queue_publication_failures_have_a_terminal_attempt_cap() -> None:
    now = datetime.now(UTC)

    retry = whatsapp_dispatch_failure_state(2, 4, now)
    exhausted = whatsapp_dispatch_failure_state(3, 4, now)

    assert retry[:2] == (3, "failed")
    assert retry[2] is not None
    assert exhausted[:2] == (4, "dispatch_failed")
    assert exhausted[2] is None


def test_expired_meta_token_requires_reconnection() -> None:
    connection = WhatsAppConnection(
        tenant_id=uuid4(),
        agent_id=uuid4(),
        waba_id="555",
        phone_number_id="777",
        access_token_encrypted=b"encrypted",
        token_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    assert not _token_is_current(connection)


def test_meta_graph_errors_distinguish_transient_and_terminal_failures() -> None:
    assert WhatsAppGraphError("send", status_code=429).retryable
    assert WhatsAppGraphError("send", status_code=503).retryable
    assert WhatsAppGraphError("send").retryable
    for code in (
        1,
        2,
        4,
        17,
        32,
        341,
        613,
        80004,
        80007,
        130429,
        131000,
        131016,
        131048,
        131056,
        131057,
    ):
        assert WhatsAppGraphError("send", status_code=400, meta_code=code).retryable
    assert not WhatsAppGraphError("send", status_code=400).retryable
    assert WhatsAppGraphError("send", status_code=400, is_transient=True).retryable
    expired = WhatsAppGraphError("send", status_code=400, meta_code=190)
    assert expired.requires_reconnect
    assert not expired.retryable
    forbidden = WhatsAppGraphError("send", status_code=403, meta_code=2)
    assert forbidden.requires_reconnect
    assert not forbidden.retryable


async def test_signup_session_is_principal_bound_and_single_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = whatsapp_settings()
    monkeypatch.setattr(integrations_router, "get_settings", lambda: settings)
    set_once = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(shared_redis_services.client, "set", set_once)
    user_id = uuid4()
    tenant_id = uuid4()
    signup_session, _ = create_whatsapp_signup_token(user_id, tenant_id, settings)

    await _consume_whatsapp_signup_session(signup_session, user_id=user_id, tenant_id=tenant_id)
    with pytest.raises(HTTPException) as replay:
        await _consume_whatsapp_signup_session(signup_session, user_id=user_id, tenant_id=tenant_id)
    assert replay.value.status_code == 409

    with pytest.raises(HTTPException) as wrong_tenant:
        await _consume_whatsapp_signup_session(signup_session, user_id=user_id, tenant_id=uuid4())
    assert wrong_tenant.value.status_code == 400


async def test_setup_serializes_by_tenant_and_phone_before_meta_side_effects() -> None:
    tenant_one, tenant_two = uuid4(), uuid4()
    principal_one = Principal(uuid4(), tenant_one, Role.OWNER, "token-one")
    principal_two = Principal(uuid4(), tenant_two, Role.OWNER, "token-two")

    def request(phone_number_id: str) -> WhatsAppCompleteRequest:
        return WhatsAppCompleteRequest(
            signup_session="x" * 20,
            code="authorization-code",
            waba_id="555",
            phone_number_id=phone_number_id,
            agent_id=uuid4(),
            two_step_verification_pin="123456",
        )

    active = serialize_whatsapp_setup(request("777"), principal_one)
    await anext(active)
    try:
        same_tenant = serialize_whatsapp_setup(request("888"), principal_one)
        with pytest.raises(HTTPException) as tenant_conflict:
            await anext(same_tenant)
        assert tenant_conflict.value.status_code == 409

        same_phone = serialize_whatsapp_setup(request("777"), principal_two)
        with pytest.raises(HTTPException) as phone_conflict:
            await anext(same_phone)
        assert phone_conflict.value.status_code == 409
    finally:
        await active.aclose()


async def test_cloud_client_validates_and_completes_embedded_signup() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/oauth/access_token"):
            assert request.method == "GET"
            assert request.url.params["client_secret"] == (
                "meta-app-secret-used-only-in-tests"  # noqa: S105
            )
            return httpx.Response(200, json={"access_token": "EA-user-token"})
        if path.endswith("/debug_token"):
            assert request.headers["authorization"] == "Bearer 123456789|meta-app-secret-used-only-in-tests"
            return httpx.Response(
                200,
                json={
                    "data": {
                        "is_valid": True,
                        "app_id": "123456789",
                        "scopes": [
                            "whatsapp_business_management",
                            "whatsapp_business_messaging",
                        ],
                        "expires_at": int((datetime.now(UTC) + timedelta(days=30)).timestamp()),
                        "data_access_expires_at": int((datetime.now(UTC) + timedelta(days=10)).timestamp()),
                    }
                },
            )
        if path.endswith("/555/phone_numbers"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "777",
                            "display_phone_number": "+1 555 0100",
                            "verified_name": "Northstar Test",
                        }
                    ]
                },
            )
        if path.endswith("/777/register"):
            assert json.loads(request.content) == {
                "messaging_product": "whatsapp",
                "pin": "123456",
            }
            return httpx.Response(200, json={"success": True})
        if path.endswith("/555/subscribed_apps"):
            return httpx.Response(200, json={"success": True})
        raise AssertionError(f"Unexpected Meta request: {request.method} {request.url}")

    client = WhatsAppCloudClient(whatsapp_settings(), transport=httpx.MockTransport(handler))
    setup = await client.complete_setup(
        code="short-lived-authorization-code",
        waba_id="555",
        phone_number_id="777",
        registration_pin="123456",
    )

    assert setup.display_phone_number == "+1 555 0100"
    assert setup.verified_name == "Northstar Test"
    assert setup.expires_at is not None
    assert setup.expires_at < datetime.now(UTC) + timedelta(days=11)
    assert [request.url.path.rsplit("/", 1)[-1] for request in requests] == [
        "access_token",
        "debug_token",
        "phone_numbers",
        "register",
        "subscribed_apps",
    ]
    for request in requests[2:]:
        assert request.headers["authorization"] == "Bearer EA-user-token"
        assert len(request.url.params["appsecret_proof"]) == 64


async def test_cloud_client_rejects_a_token_without_required_scopes() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "is_valid": True,
                    "app_id": "123456789",
                    "scopes": ["whatsapp_business_management"],
                }
            },
        )

    client = WhatsAppCloudClient(whatsapp_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(WhatsAppGraphError, match="permission"):
        await client.validate_token("EA-user-token")


async def test_cloud_client_honors_meta_transient_error_flag() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"code": 999999, "is_transient": True}},
        )

    client = WhatsAppCloudClient(whatsapp_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(WhatsAppGraphError) as captured:
        await client.send_text(
            "EA-user-token",
            phone_number_id="777",
            to="15550101",
            text="Retry this provider outage",
        )

    assert captured.value.meta_code == 999999
    assert captured.value.is_transient
    assert captured.value.retryable


def test_extracts_text_button_and_interactive_messages() -> None:
    payload = _webhook_payload(
        [
            {"id": "wamid.1", "from": "15550101", "type": "text", "text": {"body": "Hello"}},
            {
                "id": "wamid.2",
                "from": "15550101",
                "type": "button",
                "button": {"text": "Yes"},
            },
            {
                "id": "wamid.3",
                "from": "15550101",
                "type": "interactive",
                "interactive": {
                    "type": "list_reply",
                    "list_reply": {"id": "choice-1", "title": "Billing"},
                },
            },
        ]
    )

    result = webhook_router.extract_inbound_messages(payload)

    assert [item.message_text for item in result] == ["Hello", "Yes", "Billing"]
    assert all(item.sender_name == "Test Customer" for item in result)


async def test_provider_message_duplicate_survives_phone_transfer_between_tenants(
    client: AsyncClient,
) -> None:
    del client  # The fixture initializes the shared test schema.
    await _clear_whatsapp_state()
    old_tenant = Tenant(slug=f"old-{uuid4().hex}", name="Old WhatsApp tenant")
    new_tenant = Tenant(slug=f"new-{uuid4().hex}", name="New WhatsApp tenant")
    async with SessionFactory() as session:
        session.add_all((old_tenant, new_tenant))
        await session.flush()
        old_tenant_id = old_tenant.id
        new_tenant_id = new_tenant.id
        old_agent = Agent(tenant_id=old_tenant.id, name="Old agent")
        new_agent = Agent(tenant_id=new_tenant.id, name="New agent")
        session.add_all((old_agent, new_agent))
        await session.flush()
        old_connection = WhatsAppConnection(
            tenant_id=old_tenant.id,
            agent_id=old_agent.id,
            waba_id="100",
            phone_number_id="777",
            access_token_encrypted=b"old-token",
        )
        session.add(old_connection)
        await session.flush()
        old_receipt = WhatsAppInboundMessage(
            tenant_id=old_tenant.id,
            connection_id=old_connection.id,
            phone_number_id="777",
            provider_message_id="wamid.globally-unique-transfer",
            sender_wa_id="15550101",
            message_type="text",
            message_text="Already handled",
            status="processed",
        )
        session.add(old_receipt)
        await session.commit()

        old_receipt.connection_id = None
        await session.delete(old_connection)
        await session.commit()
        new_connection = WhatsAppConnection(
            tenant_id=new_tenant.id,
            agent_id=new_agent.id,
            waba_id="200",
            phone_number_id="777",
            access_token_encrypted=b"new-token",
        )
        session.add(new_connection)
        await session.commit()

        result = await _durably_receive(
            session,
            new_connection,
            InboundEnvelope(
                phone_number_id="777",
                provider_message_id="wamid.globally-unique-transfer",
                sender_wa_id="15550101",
                sender_name="Test Customer",
                message_type="text",
                message_text="Already handled",
                provider_timestamp=datetime.now(UTC),
                supported=True,
            ),
        )
        assert result is None
        matching = (
            await session.scalars(
                select(WhatsAppInboundMessage).where(
                    WhatsAppInboundMessage.provider_message_id == "wamid.globally-unique-transfer"
                )
            )
        ).all()
        assert len(matching) == 1

        await session.execute(delete(WhatsAppConnection))
        await session.execute(delete(WhatsAppInboundMessage))
        await session.execute(delete(Agent).where(Agent.tenant_id.in_((old_tenant_id, new_tenant_id))))
        await session.execute(delete(Tenant).where(Tenant.id.in_((old_tenant_id, new_tenant_id))))
        await session.commit()


async def _clear_whatsapp_state() -> None:
    async with SessionFactory() as session:
        await session.execute(delete(WhatsAppOutboundDelivery))
        await session.execute(delete(WhatsAppInboundMessage))
        await session.execute(delete(WhatsAppConnection))
        await session.execute(
            delete(IntegrationConnection).where(IntegrationConnection.integration_id == "whatsapp")
        )
        await session.commit()


async def test_workspace_can_manage_one_dedicated_number_per_bot(
    client: AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _clear_whatsapp_state()
    monkeypatch.setattr(integrations_router, "get_settings", whatsapp_settings)
    agent_ids: list[UUID] = []
    for name in ("Support WhatsApp Bot", "Sales WhatsApp Bot"):
        created = await client.post(
            "/api/v1/agents",
            headers=auth_headers,
            json={"name": name, "description": "Dedicated WhatsApp routing test"},
        )
        assert created.status_code == 201, created.text
        agent_ids.append(UUID(created.json()["id"]))

    token = auth_headers["Authorization"].removeprefix("Bearer ")
    tenant_id = UUID(decode_token(token, "access")["tid"])
    async with SessionFactory() as session:
        session.add_all(
            [
                WhatsAppConnection(
                    tenant_id=tenant_id,
                    agent_id=agent_ids[0],
                    waba_id="555",
                    phone_number_id="777",
                    display_phone_number="+1 555 0100",
                    verified_name="Support Business",
                    access_token_encrypted=b"support-token",
                    status="connected",
                ),
                WhatsAppConnection(
                    tenant_id=tenant_id,
                    agent_id=agent_ids[1],
                    waba_id="555",
                    phone_number_id="888",
                    display_phone_number="+1 555 0200",
                    verified_name="Sales Business",
                    access_token_encrypted=b"sales-token",
                    status="connected",
                ),
            ]
        )
        await session.commit()

    status = await client.get("/api/v1/integrations/whatsapp/status", headers=auth_headers)
    assert status.status_code == 200, status.text
    assert status.json()["connected"] is True
    assert {item["agentId"] for item in status.json()["connections"]} == {
        str(agent_ids[0]),
        str(agent_ids[1]),
    }

    disconnected = await client.delete(
        f"/api/v1/integrations/whatsapp/{agent_ids[0]}", headers=auth_headers
    )
    assert disconnected.status_code == 204, disconnected.text
    remaining = await client.get("/api/v1/integrations/whatsapp/status", headers=auth_headers)
    assert remaining.json()["connected"] is True
    assert [item["agentId"] for item in remaining.json()["connections"]] == [str(agent_ids[1])]
    await _clear_whatsapp_state()


async def test_embedded_signup_and_signed_idempotent_webhook(
    client: AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _clear_whatsapp_state()
    settings = whatsapp_settings()
    monkeypatch.setattr(integrations_router, "get_settings", lambda: settings)
    monkeypatch.setattr(webhook_router, "get_settings", lambda: settings)

    created_agent = await client.post(
        "/api/v1/agents",
        headers=auth_headers,
        json={"name": "WhatsApp Integration Agent", "description": "WhatsApp test"},
    )
    assert created_agent.status_code == 201, created_agent.text
    agent_id = created_agent.json()["id"]
    draft_bootstrap = await client.get("/api/v1/integrations/whatsapp/bootstrap", headers=auth_headers)
    draft_connection = await client.post(
        "/api/v1/integrations/whatsapp/complete",
        headers=auth_headers,
        json={
            "signupSession": draft_bootstrap.json()["signupSession"],
            "code": "authorization-code-from-facebook",
            "wabaId": "555",
            "phoneNumberId": "777",
            "agentId": agent_id,
            "twoStepVerificationPin": "123456",
        },
    )
    assert draft_connection.status_code == 409
    assert "Publish the agent" in draft_connection.text
    activated_agent = await client.patch(
        f"/api/v1/agents/{agent_id}",
        headers=auth_headers,
        json={"status": "active"},
    )
    assert activated_agent.status_code == 200, activated_agent.text

    async def fake_complete_setup(**kwargs: Any) -> WhatsAppSetup:
        assert kwargs == {
            "code": "authorization-code-from-facebook",
            "waba_id": "555",
            "phone_number_id": "777",
            "registration_pin": "123456",
        }
        return WhatsAppSetup(
            access_token="EA-test-token",  # noqa: S106
            expires_at=datetime.now(UTC) + timedelta(days=30),
            display_phone_number="+1 555 0100",
            verified_name="Test Business",
        )

    monkeypatch.setattr(shared_whatsapp_client, "complete_setup", fake_complete_setup)
    monkeypatch.setattr(shared_token_cipher, "encrypt", lambda _: b"encrypted-token")

    bootstrap = await client.get("/api/v1/integrations/whatsapp/bootstrap", headers=auth_headers)
    assert bootstrap.status_code == 200
    assert bootstrap.json()["appId"] == "123456789"
    assert bootstrap.json()["configurationId"] == "987654321"
    assert bootstrap.json()["apiVersion"] == "v26.0"
    assert bootstrap.json()["signupSession"]

    connected = await client.post(
        "/api/v1/integrations/whatsapp/complete",
        headers=auth_headers,
        json={
            "signupSession": bootstrap.json()["signupSession"],
            "code": "authorization-code-from-facebook",
            "wabaId": "555",
            "phoneNumberId": "777",
            "agentId": agent_id,
            "twoStepVerificationPin": "123456",
        },
    )
    assert connected.status_code == 200, connected.text
    assert connected.json()["phoneNumberId"] == "777"
    assert connected.json()["agentId"] == agent_id

    fake_toggle = await client.patch(
        "/api/v1/integrations/whatsapp",
        headers=auth_headers,
        json={"connected": False},
    )
    assert fake_toggle.status_code == 409

    published: list[str] = []

    def fake_apply_async(*, args: list[str], **_: Any) -> object:
        published.extend(args)
        return object()

    monkeypatch.setattr(inbound_task, "apply_async", fake_apply_async)
    body = json.dumps(
        _webhook_payload(
            [
                {
                    "id": "wamid.idempotent-1",
                    "from": "15550101",
                    "timestamp": "1788451200",
                    "type": "text",
                    "text": {"body": "What are your hours?"},
                }
            ]
        ),
        separators=(",", ":"),
    ).encode()
    app_secret = settings.meta_app_secret
    assert app_secret is not None
    signature = "sha256=" + hmac.new(app_secret.get_secret_value().encode(), body, hashlib.sha256).hexdigest()
    webhook = await client.post(
        "/api/v1/webhooks/whatsapp",
        content=body,
        headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"},
    )
    assert webhook.status_code == 200, webhook.text
    assert len(published) == 1

    duplicate = await client.post(
        "/api/v1/webhooks/whatsapp",
        content=body,
        headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"},
    )
    assert duplicate.status_code == 200
    assert len(published) == 1

    invalid = await client.post(
        "/api/v1/webhooks/whatsapp",
        content=body,
        headers={"X-Hub-Signature-256": "sha256=" + ("0" * 64)},
    )
    assert invalid.status_code == 401

    verified = await client.get(
        "/api/v1/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "webhook-verify-token-used-only-in-tests",
            "hub.challenge": "challenge-value",
        },
    )
    assert verified.status_code == 200
    assert verified.text == "challenge-value"

    token = auth_headers["Authorization"].removeprefix("Bearer ")
    tenant_id = UUID(decode_token(token, "access")["tid"])
    async with SessionFactory() as session:
        receipt = await session.scalar(
            select(WhatsAppInboundMessage).where(
                WhatsAppInboundMessage.tenant_id == tenant_id,
                WhatsAppInboundMessage.provider_message_id == "wamid.idempotent-1",
            )
        )
        assert receipt is not None
        assert receipt.message_text == "What are your hours?"
        assert receipt.dispatch_attempts == 1

    disconnected = await client.delete("/api/v1/integrations/whatsapp", headers=auth_headers)
    assert disconnected.status_code == 204
    status = await client.get("/api/v1/integrations/whatsapp/status", headers=auth_headers)
    assert status.status_code == 200
    assert status.json()["connected"] is False
    async with SessionFactory() as session:
        preserved_receipt = await session.scalar(
            select(WhatsAppInboundMessage).where(
                WhatsAppInboundMessage.provider_message_id == "wamid.idempotent-1"
            )
        )
        assert preserved_receipt is not None
        assert preserved_receipt.status == "cancelled"
        assert preserved_receipt.connection_id is None
        assert preserved_receipt.phone_number_id == "777"


async def test_inbound_worker_reuses_thread_and_sends_chunked_grounded_reply(
    client: AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _clear_whatsapp_state()
    created_agent = await client.post(
        "/api/v1/agents",
        headers=auth_headers,
        json={"name": "WhatsApp Worker Agent", "description": "Worker test"},
    )
    assert created_agent.status_code == 201
    agent_id = UUID(created_agent.json()["id"])
    activated = await client.patch(
        f"/api/v1/agents/{agent_id}", headers=auth_headers, json={"status": "active"}
    )
    assert activated.status_code == 200
    token = auth_headers["Authorization"].removeprefix("Bearer ")
    tenant_id = UUID(decode_token(token, "access")["tid"])
    async with SessionFactory() as session:
        connection = WhatsAppConnection(
            tenant_id=tenant_id,
            agent_id=agent_id,
            waba_id="555",
            phone_number_id="777",
            display_phone_number="+1 555 0100",
            verified_name="Worker Test",
            access_token_encrypted=b"encrypted-token",
            status="connected",
        )
        session.add(connection)
        await session.flush()
        conversation = Conversation(
            tenant_id=tenant_id,
            agent_id=agent_id,
            visitor_id="15550101",
            visitor_name="Earlier Name",
            channel=Channel.WHATSAPP,
        )
        session.add(conversation)
        await session.flush()
        receipt = WhatsAppInboundMessage(
            tenant_id=tenant_id,
            connection_id=connection.id,
            phone_number_id=connection.phone_number_id,
            provider_message_id="wamid.worker-1",
            sender_wa_id="15550101",
            sender_name="Current Customer",
            message_type="text",
            message_text="Please explain your plans",
            provider_timestamp=datetime.now(UTC),
            status="queued",
        )
        session.add(receipt)
        await session.commit()
        receipt_id = receipt.id
        conversation_id = conversation.id

    observed: dict[str, Any] = {}

    async def fake_prepare(session: object, **kwargs: Any) -> PreparedChat:
        del session
        observed.update(kwargs)
        request = kwargs["request"]
        assert request.conversation_id == conversation_id
        return PreparedChat(conversation_id, uuid4(), "A" * 5000, [])

    delivered_chunks: list[str] = []

    async def fake_send_text(_: str, **kwargs: Any) -> str:
        delivered_chunks.append(kwargs["text"])
        return f"wamid.out-{len(delivered_chunks)}"

    monkeypatch.setattr(chat_coordinator, "prepare", fake_prepare)
    monkeypatch.setattr(shared_token_cipher, "decrypt", lambda _: "EA-worker-token")
    monkeypatch.setattr(shared_whatsapp_client, "send_text", fake_send_text)

    await _process_whatsapp_inbound_async(receipt_id)

    assert observed["channel"] == Channel.WHATSAPP
    assert len(delivered_chunks) == 2
    assert all(len(chunk) <= MAX_WHATSAPP_TEXT_LENGTH for chunk in delivered_chunks)
    async with SessionFactory() as session:
        persisted = await session.get(WhatsAppInboundMessage, receipt_id)
        thread = await session.get(Conversation, conversation_id)
        assert persisted is not None
        assert persisted.status == "processed"
        assert persisted.conversation_id == conversation_id
        assert persisted.outbound_message_ids == ["wamid.out-1", "wamid.out-2"]
        assert thread is not None
        assert thread.visitor_name == "Current Customer"
        assert thread.unread_count == 1
        persisted.status = "rejected"
        await session.commit()

    # A late duplicate task must not re-run an already-terminal receipt.
    await _process_whatsapp_inbound_async(receipt_id)
    assert len(delivered_chunks) == 2
    async with SessionFactory() as session:
        persisted = await session.get(WhatsAppInboundMessage, receipt_id)
        assert persisted is not None
        # A supported inbound message still opens the customer-service window
        # when AI delivery failed, allowing a teammate to recover the thread.
        persisted.status = "failed"
        persisted.provider_timestamp = datetime.now(UTC)
        await session.commit()

    queued_deliveries: list[str] = []

    def fake_outbound_apply_async(*, args: list[str], **_: Any) -> object:
        queued_deliveries.extend(args)
        return object()

    monkeypatch.setattr(outbound_task, "apply_async", fake_outbound_apply_async)
    human_reply = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=auth_headers,
        json={"content": "A teammate reply"},
    )
    assert human_reply.status_code == 201, human_reply.text
    assert len(queued_deliveries) == 1
    async with SessionFactory() as session:
        delivery = await session.scalar(
            select(WhatsAppOutboundDelivery).where(
                WhatsAppOutboundDelivery.message_id == UUID(human_reply.json()["id"])
            )
        )
        assert delivery is not None
        assert delivery.status == "queued"
        delivery_id = delivery.id

    await _send_whatsapp_human_reply_async(delivery_id)
    async with SessionFactory() as session:
        sent_delivery = await session.get(WhatsAppOutboundDelivery, delivery_id)
        assert sent_delivery is not None
        assert sent_delivery.status == "sent"
        receipt = await session.get(WhatsAppInboundMessage, receipt_id)
        assert receipt is not None
        sent_delivery.status = "rejected"
        receipt.provider_timestamp = datetime.now(UTC) - timedelta(hours=25)
        await session.commit()

    # Re-published terminal outbound rows are no-ops.
    await _send_whatsapp_human_reply_async(delivery_id)
    assert len(delivered_chunks) == 3

    outside_window = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=auth_headers,
        json={"content": "This requires a template"},
    )
    assert outside_window.status_code == 409
    assert "24-hour" in outside_window.text

    async with SessionFactory() as session:
        connection = await session.scalar(
            select(WhatsAppConnection).where(WhatsAppConnection.tenant_id == tenant_id)
        )
        assert connection is not None
        connection.status = "reconnect_required"
        await session.commit()
    reconnect_status = await client.get("/api/v1/integrations/whatsapp/status", headers=auth_headers)
    assert reconnect_status.status_code == 200
    assert reconnect_status.json()["connected"] is False
    assert reconnect_status.json()["connection"]["status"] == "reconnect_required"

    async with SessionFactory() as session:
        delivery = await session.get(WhatsAppOutboundDelivery, delivery_id)
        assert delivery is not None
        delivery.status = "queued"
        message = await session.get(Message, delivery.message_id)
        assert message is not None
        message.finish_reason = "queued"
        connection = await session.scalar(
            select(WhatsAppConnection).where(WhatsAppConnection.tenant_id == tenant_id)
        )
        assert connection is not None
        crash_window_thread = Conversation(
            tenant_id=tenant_id,
            agent_id=agent_id,
            visitor_id="15550999",
            channel=Channel.WHATSAPP,
        )
        unrelated_channel_thread = Conversation(
            tenant_id=tenant_id,
            agent_id=agent_id,
            visitor_id="15550999",
            channel=Channel.API,
        )
        unrelated_sender_thread = Conversation(
            tenant_id=tenant_id,
            agent_id=agent_id,
            visitor_id="15550888",
            channel=Channel.WHATSAPP,
        )
        session.add_all((crash_window_thread, unrelated_channel_thread, unrelated_sender_thread))
        await session.flush()
        session.add(
            WhatsAppInboundMessage(
                tenant_id=tenant_id,
                connection_id=connection.id,
                phone_number_id=connection.phone_number_id,
                provider_message_id="wamid.unlinked-crash-window",
                sender_wa_id="15550999",
                message_type="text",
                message_text="Worker crashed after creating the thread",
                provider_timestamp=datetime.now(UTC),
                status="processing",
                conversation_id=None,
            )
        )
        crash_window_thread_id = crash_window_thread.id
        unrelated_channel_thread_id = unrelated_channel_thread.id
        unrelated_sender_thread_id = unrelated_sender_thread.id
        # A repeated DELETE must recover cleanup after a phase-one interruption.
        connection.status = "disconnecting"
        await session.commit()

    disconnected = await client.delete("/api/v1/integrations/whatsapp", headers=auth_headers)
    assert disconnected.status_code == 204
    async with SessionFactory() as session:
        preserved_delivery = await session.get(WhatsAppOutboundDelivery, delivery_id)
        preserved_receipt = await session.get(WhatsAppInboundMessage, receipt_id)
        assert preserved_delivery is not None
        assert preserved_delivery.status == "cancelled"
        assert preserved_delivery.connection_id is None
        assert preserved_delivery.phone_number_id == "777"
        assert preserved_receipt is not None
        assert preserved_receipt.status == "cancelled"
        assert preserved_receipt.connection_id is None
        assert preserved_receipt.phone_number_id == "777"
        message = await session.get(Message, preserved_delivery.message_id)
        assert message is not None
        assert message.finish_reason == "cancelled"
        old_thread = await session.get(Conversation, conversation_id)
        assert old_thread is not None
        assert old_thread.state == ConversationState.RESOLVED
        assert old_thread.ended_at is not None
        assert old_thread.resolution == "whatsapp_disconnected"
        crash_window_thread = await session.get(Conversation, crash_window_thread_id)
        unrelated_channel_thread = await session.get(Conversation, unrelated_channel_thread_id)
        unrelated_sender_thread = await session.get(Conversation, unrelated_sender_thread_id)
        assert crash_window_thread is not None
        assert crash_window_thread.state == ConversationState.RESOLVED
        assert crash_window_thread.resolution == "whatsapp_disconnected"
        assert unrelated_channel_thread is not None
        assert unrelated_channel_thread.state != ConversationState.RESOLVED
        assert unrelated_sender_thread is not None
        assert unrelated_sender_thread.state != ConversationState.RESOLVED
        session.add(
            WhatsAppConnection(
                tenant_id=tenant_id,
                agent_id=agent_id,
                waba_id="555",
                phone_number_id="777",
                display_phone_number="+1 555 0100",
                verified_name="Reconnected Test",
                access_token_encrypted=b"new-encrypted-token",
                status="connected",
            )
        )
        await session.commit()

    # Old receipts cannot open the 24-hour window for a replacement connection.
    stale_thread_reply = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=auth_headers,
        json={"content": "Must not use the old connection window"},
    )
    assert stale_thread_reply.status_code == 409
    assert "24-hour" in stale_thread_reply.text

    await _clear_whatsapp_state()


def _webhook_payload(messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "555",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "777"},
                            "contacts": [
                                {
                                    "wa_id": "15550101",
                                    "profile": {"name": "Test Customer"},
                                }
                            ],
                            "messages": messages,
                        },
                    }
                ],
            }
        ],
    }
