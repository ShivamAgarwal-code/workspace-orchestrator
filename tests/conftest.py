"""Shared pytest fixtures.

Unit tests (tests/unit) are pure-Python and need no external services. Integration tests
(tests/integration) need the real Postgres+pgvector and Redis from docker-compose — run them with
`docker compose exec api pytest tests/integration` (see README) since hybrid search is raw SQL
against pgvector, which nothing short of a real Postgres instance can meaningfully fake.
"""
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete

from app.agents.mock_clients import reset_mock_store
from app.cache.redis_client import close_redis
from app.db.base import dispose_engine, session_scope
from app.db.models import (
    AuditLog,
    Conversation,
    GCalCache,
    GDriveCache,
    GmailCache,
    SyncStatus,
    User,
)


@pytest.fixture(autouse=True)
async def _fresh_event_loop_clients():
    """pytest-asyncio gives each test function its own event loop, but the DB engine and Redis
    client are process-wide singletons cached across tests (matching how they behave in
    production, under one persistent loop). Without resetting them here, test N+1's new loop
    tries to reuse test N's loop-bound connections and dies with "Future attached to a different
    loop" / "Event loop is closed". See app.db.base.dispose_engine for the full explanation."""
    yield
    await dispose_engine()
    await close_redis()


@pytest.fixture
def test_user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
async def test_user(test_user_id):
    """A throwaway user, isolated from the seeded demo user, cleaned up after the test."""
    email = f"test-{test_user_id}@example.com"
    async with session_scope() as session:
        session.add(User(id=test_user_id, email=email, timezone="UTC"))

    yield test_user_id

    async with session_scope() as session:
        for model in (AuditLog, Conversation, GmailCache, GCalCache, GDriveCache, SyncStatus):
            await session.execute(delete(model).where(model.user_id == test_user_id))
        await session.execute(delete(User).where(User.id == test_user_id))

    reset_mock_store(str(test_user_id))


@pytest.fixture
def frozen_now() -> datetime:
    """A fixed, timezone-aware 'now' for deterministic temporal-reasoning tests: Thursday
    2026-08-20 12:00 UTC."""
    return datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
