"""FastAPI dependencies shared across routers.

Auth simplification: this assignment scopes out a full session/JWT auth layer, so the current
user is resolved from an `X-User-Id` header (falling back to the seeded demo user for easy
curl/Postman testing). In a production build this would be replaced by a session cookie or JWT
resolved to a user id — every downstream component (agents, orchestrator, cache keys, rate
limiter, audit log) already takes a `user_id`/`User` and has no other assumption about how it was
authenticated, so that swap is localized entirely to this function.
"""
from uuid import UUID

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import DEMO_USER_EMAIL, DEMO_USER_ID
from app.db.base import get_db
from app.db.models import User


async def get_current_user(
    x_user_id: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db),
) -> User:
    user_id = UUID(x_user_id) if x_user_id else DEMO_USER_ID

    user = await session.get(User, user_id)
    if user is not None:
        return user

    if user_id == DEMO_USER_ID:
        user = User(id=DEMO_USER_ID, email=DEMO_USER_EMAIL, timezone="UTC")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user

    raise HTTPException(status_code=404, detail=f"unknown user_id: {user_id}")
