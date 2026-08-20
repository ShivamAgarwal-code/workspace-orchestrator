"""Conversation-history persistence: last N turns per user, used both to ground the intent
classifier's reference resolution and to answer follow-ups like "that email"."""
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Conversation


async def get_recent_conversations(session: AsyncSession, user_id: UUID) -> list[dict]:
    settings = get_settings()
    stmt = (
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(desc(Conversation.created_at))
        .limit(settings.conversation_history_size)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "query": c.query,
            "intent": c.intent,
            "response": c.response,
            "actions_taken": c.actions_taken,
            "entities_referenced": c.entities_referenced,
        }
        for c in reversed(rows)  # oldest -> newest, matching how the classifier prompt reads
    ]


async def save_conversation(
    session: AsyncSession,
    user_id: UUID,
    query: str,
    intent: dict,
    response: str,
    actions_taken: list[str],
    entities_referenced: dict,
) -> Conversation:
    conv = Conversation(
        user_id=user_id,
        query=query,
        intent=intent,
        response=response,
        actions_taken=actions_taken,
        entities_referenced=entities_referenced,
    )
    session.add(conv)
    await session.commit()
    await session.refresh(conv)
    return conv
