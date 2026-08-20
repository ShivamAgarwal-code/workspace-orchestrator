"""Refreshes and persists Google OAuth access tokens, refreshing ~2 minutes before expiry so an
in-flight orchestration never hits a 401 mid-DAG-execution."""
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.google_oauth import refresh_access_token
from app.config import get_settings
from app.db.models import User

_EXPIRY_BUFFER = timedelta(minutes=2)


class TokenManager:
    async def get_credentials(self, session: AsyncSession, user: User):
        from google.oauth2.credentials import Credentials

        settings = get_settings()
        if not user.google_refresh_token:
            raise ValueError(f"user {user.id} has not completed Google OAuth")

        if not user.google_token_expiry or user.google_token_expiry <= datetime.now(UTC) + _EXPIRY_BUFFER:
            refreshed = refresh_access_token(user.google_refresh_token)
            user.google_access_token = refreshed["access_token"]
            user.google_token_expiry = refreshed["expiry"]
            await session.commit()

        return Credentials(
            token=user.google_access_token,
            refresh_token=user.google_refresh_token,
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            token_uri="https://oauth2.googleapis.com/token",
        )
