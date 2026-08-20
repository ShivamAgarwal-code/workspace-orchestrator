import re
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentError, BaseAgent, SearchResult
from app.db.models import User
from app.llm.base import EmbeddingProvider
from app.search import vector_store
from app.utils.temporal import resolve_date_phrase

_BOOKING_REF_RE = re.compile(r"\b([A-Z]{2}\d{3,6})\b")


class GmailAgent(BaseAgent):
    service_name = "gmail"

    def __init__(self, session: AsyncSession, user: User, embeddings: EmbeddingProvider, client):
        self._session = session
        self._user = user
        self._embeddings = embeddings
        self._client = client

    async def search(self, query: str, filters: dict | None = None, limit: int = 10) -> list[SearchResult]:
        filters = filters or {}
        sender = filters.get("sender") or _first(filters.get("email_addresses"))
        since, until = resolve_date_phrase(filters.get("date_phrase"), datetime.now(UTC), self._user.timezone)
        embedding = await self._embeddings.embed_one(query or "")
        rows = await vector_store.search_gmail(
            self._session, self._user.id, query, embedding, sender=sender, since=since, until=until, limit=limit,
        )
        return [self._to_search_result(r) for r in rows]

    def _to_search_result(self, row: dict) -> SearchResult:
        body = row.get("body_preview") or ""
        return SearchResult(
            id=row["email_id"],
            service="gmail",
            title=row.get("subject") or "(no subject)",
            snippet=body[:280],
            score=float(row.get("fused_score") or 0.0),
            metadata={
                "sender": row.get("sender"),
                "recipients": row.get("recipients"),
                "thread_id": row.get("thread_id"),
                "labels": row.get("labels"),
                "booking_reference": _extract_booking_ref(body),
            },
            timestamp=row.get("received_at"),
        )

    async def get_context(self, item_id: str) -> dict:
        try:
            return await self._client.get_message(item_id)
        except KeyError as e:
            raise AgentError(str(e), retryable=False) from e

    async def execute(self, action: str, params: dict) -> dict:
        if action == "send_email":
            return await self._client.send_message(
                params["to"], params["subject"], params["body"], params.get("thread_id")
            )
        if action == "draft_email":
            return await self._client.create_draft(params["to"], params["subject"], params["body"])
        if action == "update_labels":
            return await self._client.modify_labels(params["message_id"], params.get("add"), params.get("remove"))
        raise AgentError(f"unknown gmail action: {action}", retryable=False)


def _first(values: list | None):
    return values[0] if values else None


def _extract_booking_ref(text: str) -> str | None:
    match = _BOOKING_REF_RE.search(text)
    return match.group(1) if match else None
