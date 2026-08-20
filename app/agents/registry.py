"""Builds the three request-scoped service agents (sharing one DB session, embedding provider,
and mock-or-real client bundle) for a given user."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.agents.client_factory import get_client_bundle
from app.agents.drive_agent import DriveAgent
from app.agents.gcal_agent import GCalAgent
from app.agents.gmail_agent import GmailAgent
from app.db.models import User
from app.llm.factory import get_embedding_provider


async def build_agents(session: AsyncSession, user: User) -> dict[str, BaseAgent]:
    gmail_client, calendar_client, drive_client = await get_client_bundle(session, user)
    embeddings = get_embedding_provider()
    return {
        "gmail": GmailAgent(session, user, embeddings, gmail_client),
        "gcal": GCalAgent(session, user, embeddings, calendar_client),
        "gdrive": DriveAgent(session, user, embeddings, drive_client),
    }
