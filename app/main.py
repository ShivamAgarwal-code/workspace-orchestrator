"""FastAPI application entry point: wires together routers, middleware, and structured logging."""
import logging

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import auth, health, query, sync, ws
from app.config import get_settings
from app.middleware.error_handler import unhandled_exception_handler
from app.middleware.logging_middleware import RequestLoggingMiddleware


def _configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(settings.log_level)),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


_configure_logging()

app = FastAPI(
    title="Agentic Google Workspace Orchestrator",
    description="Natural-language orchestration over Gmail, Google Calendar, and Google Drive.",
    version="0.1.0",
)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.add_middleware(RequestLoggingMiddleware)
app.add_exception_handler(Exception, unhandled_exception_handler)

app.include_router(health.router)
app.include_router(query.router)
app.include_router(auth.router)
app.include_router(sync.router)
app.include_router(ws.router)
