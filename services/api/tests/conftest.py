from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

Path("northstar-test.db").unlink(missing_ok=True)

os.environ.update(
    {
        "APP_ENV": "test",
        "DATABASE_URL": "sqlite+aiosqlite:///./northstar-test.db",
        "REDIS_URL": "redis://127.0.0.1:6399/15",
        "APP_AUTO_CREATE_SCHEMA": "true",
        "JWT_SECRET": "test-only-secret-with-more-than-thirty-two-characters",
        "SEED_ADMIN_EMAIL": "owner@example.com",
        "SEED_ADMIN_PASSWORD": "Correct-Horse-Test-Password-42!",
        "SEED_ADMIN_NAME": "Test Owner",
        "SEED_TENANT_NAME": "Test Workspace",
        "ALLOW_DETERMINISTIC_EMBEDDINGS": "true",
        "RATE_LIMIT_FAIL_OPEN": "true",
        "BACKGROUND_DISPATCH_ENABLED": "false",
    }
)

from northstar_api.main import app  # noqa: E402


@pytest.fixture(scope="session")
async def client() -> AsyncIterator[AsyncClient]:
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
            yield http


@pytest.fixture(scope="session")
async def session_payload(client: AsyncClient) -> dict:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "Correct-Horse-Test-Password-42!"},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(scope="session")
async def auth_headers(session_payload: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {session_payload['accessToken']}"}
