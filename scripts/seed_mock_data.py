"""Creates the demo user and runs a full sync so the mock fixture data (emails/events/files) is
embedded and searchable immediately after `docker compose up`, without waiting for the first
Celery beat tick.

Usage: python -m scripts.seed_mock_data
"""
import asyncio
import uuid

from sqlalchemy import select

from app.constants import DEMO_USER_EMAIL, DEMO_USER_ID
from app.db.base import session_scope
from app.db.models import User
from app.sync.tasks import sync_user_now


async def ensure_demo_user() -> uuid.UUID:
    async with session_scope() as session:
        existing = await session.get(User, DEMO_USER_ID)
        if existing is None:
            existing = (await session.execute(select(User).where(User.email == DEMO_USER_EMAIL))).scalar_one_or_none()
        if existing is None:
            session.add(User(id=DEMO_USER_ID, email=DEMO_USER_EMAIL, timezone="UTC"))
            print(f"Created demo user {DEMO_USER_EMAIL} ({DEMO_USER_ID})")
        else:
            print(f"Demo user already exists: {existing.email} ({existing.id})")
    return DEMO_USER_ID


async def main() -> None:
    user_id = await ensure_demo_user()
    print("Syncing mock Gmail/Calendar/Drive fixture data...")
    summary = await sync_user_now(user_id)
    print(f"Sync complete: {summary}")


if __name__ == "__main__":
    asyncio.run(main())
