from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from northstar_api import __version__
from northstar_api.config import get_settings
from northstar_api.database import SessionFactory, close_database, initialize_schema
from northstar_api.health import router as health_router
from northstar_api.logging import configure_logging, request_id_ctx
from northstar_api.middleware import RequestContextMiddleware
from northstar_api.routers import api_router
from northstar_api.services.rate_limit import redis_services
from northstar_api.services.seed import seed_from_environment

settings = get_settings()
configure_logging(settings)
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if settings.app_auto_create_schema:
        await initialize_schema()
    async with SessionFactory() as session:
        await seed_from_environment(session, settings)
    logger.info("application_started", version=__version__, environment=settings.app_env)
    try:
        yield
    finally:
        await redis_services.close()
        await close_database()
        logger.info("application_stopped")


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    default_response_class=JSONResponse,
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.app_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Idempotency-Key",
        "X-Request-ID",
    ],
    expose_headers=["X-Request-ID", "Retry-After"],
)


def _is_public_widget_cors_path(path: str) -> bool:
    prefixes = {f"{settings.app_api_prefix.rstrip('/')}/widget/", "/v1/widget/"}
    for prefix in prefixes:
        if not path.startswith(prefix):
            continue
        parts = path[len(prefix) :].strip("/").split("/")
        return len(parts) == 2 and parts[0] != "sessions" and parts[1] in {"bootstrap", "sessions"}
    return False


def _widget_cors_headers(origin: str) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Accept, Content-Type",
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
    }


@app.middleware("http")
async def security_headers(request: Request, call_next: RequestResponseEndpoint) -> Response:
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), geolocation=(), payment=()")
    if settings.is_production:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.middleware("http")
async def public_widget_cors(request: Request, call_next: RequestResponseEndpoint) -> Response:
    """Expose bootstrap/session responses only to the browser Origin accepted by the route.

    Preflight is intentionally non-authorizing: the subsequent request still performs
    the per-agent domain check before any response receives an allow-origin header.
    """
    if not _is_public_widget_cors_path(request.url.path):
        return await call_next(request)

    from northstar_api.routers.widget import widget_request_origin

    origin = widget_request_origin(request)
    if request.method == "OPTIONS":
        requested_method = request.headers.get("access-control-request-method", "").upper()
        if not origin or requested_method not in {"GET", "POST"}:
            return Response(status_code=400)
        return Response(status_code=204, headers=_widget_cors_headers(origin))

    response = await call_next(request)
    if origin and 200 <= response.status_code < 300:
        for name, value in _widget_cors_headers(origin).items():
            response.headers[name] = value
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    message = exc.detail if isinstance(exc.detail, str) else "Request could not be completed"
    return JSONResponse(
        {"error": {"code": f"http_{exc.status_code}", "message": message, "requestId": request_id_ctx.get()}},
        status_code=exc.status_code,
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {"field": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
        for error in exc.errors()
    ]
    return JSONResponse(
        {
            "error": {
                "code": "validation_error",
                "message": "Request validation failed",
                "requestId": request_id_ctx.get(),
                "fields": errors,
            }
        },
        status_code=422,
    )


app.include_router(health_router)
app.include_router(api_router, prefix=settings.app_api_prefix)
# Compatibility alias for clients configured with the documented /v1 base.
if settings.app_api_prefix != "/v1":
    app.include_router(api_router, prefix="/v1", include_in_schema=False)


def run() -> None:
    uvicorn.run("northstar_api.main:app", host=settings.app_host, port=settings.app_port, reload=False)


if __name__ == "__main__":
    run()
