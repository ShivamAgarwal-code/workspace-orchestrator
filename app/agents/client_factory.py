"""Picks mock or real Google API clients based on settings.mock_google_api."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import User


async def get_client_bundle(session: AsyncSession, user: User) -> tuple:
    """Returns (gmail_client, calendar_client, drive_client)."""
    settings = get_settings()
    if settings.mock_google_api:
        from app.agents.mock_clients import MockCalendarClient, MockDriveClient, MockGmailClient

        uid = str(user.id)
        return MockGmailClient(uid), MockCalendarClient(uid), MockDriveClient(uid)

    from app.agents.google_clients import RealCalendarClient, RealDriveClient, RealGmailClient
    from app.auth.token_manager import TokenManager

    credentials = await TokenManager().get_credentials(session, user)
    return RealGmailClient(credentials), RealCalendarClient(credentials), RealDriveClient(credentials)
