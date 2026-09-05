from __future__ import annotations

import time
import uuid

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from northstar_api.logging import request_id_ctx
from northstar_api.metrics import HTTP_DURATION, HTTP_REQUESTS

logger = structlog.get_logger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id", "")
        if not request_id or len(request_id) > 128:
            request_id = uuid.uuid4().hex
        token = request_id_ctx.set(request_id)
        start = time.perf_counter()
        status_code = 500
        route = request.url.path
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            duration = time.perf_counter() - start
            route_object = request.scope.get("route")
            if route_object and getattr(route_object, "path", None):
                route = route_object.path
            HTTP_REQUESTS.labels(request.method, route, str(status_code)).inc()
            HTTP_DURATION.labels(request.method, route).observe(duration)
            logger.info(
                "http_request",
                method=request.method,
                path=route,
                status=status_code,
                duration_ms=round(duration * 1000, 2),
            )
            request_id_ctx.reset(token)
