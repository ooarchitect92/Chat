from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from starlette.responses import Response

from northstar_api.config import get_settings
from northstar_api.database import SessionFactory
from northstar_api.services.llm import nvidia_adapter
from northstar_api.services.rate_limit import redis_services

router = APIRouter(tags=["operations"])


@router.get("/health/live", include_in_schema=False)
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
async def readiness() -> JSONResponse:
    settings = get_settings()
    checks: dict[str, str] = {"database": "unknown", "redis": "unknown", "nvidia": "configured"}
    try:
        async with SessionFactory() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
    try:
        checks["redis"] = "ok" if await redis_services.ping() else "unavailable"
    except Exception:
        checks["redis"] = "unavailable"
    if not nvidia_adapter.configured:
        checks["nvidia"] = "not-configured"
    required_ok = checks["database"] == "ok"
    if settings.is_production:
        required_ok = (
            required_ok
            and checks["redis"] == "ok"
            and (checks["nvidia"] == "configured" or not settings.require_nvidia)
        )
    return JSONResponse(
        {"status": "ready" if required_ok else "not-ready", "checks": checks},
        status_code=200 if required_ok else 503,
    )


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
