"""Structured request logging with a per-request correlation id, useful for tracing one query
across intent classification / orchestration / synthesis log lines and, later, audit log rows."""
import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = structlog.get_logger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        start = time.monotonic()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        response = None
        try:
            response = await call_next(request)
            return response
        finally:
            duration_ms = (time.monotonic() - start) * 1000
            logger.info(
                "request",
                method=request.method,
                path=request.url.path,
                status=getattr(response, "status_code", None),
                duration_ms=round(duration_ms, 1),
            )
            if response is not None:
                response.headers["X-Request-ID"] = request_id
            structlog.contextvars.clear_contextvars()
