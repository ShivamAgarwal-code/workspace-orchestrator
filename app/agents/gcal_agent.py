from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentError, BaseAgent, SearchResult
from app.db.models import User
from app.llm.base import EmbeddingProvider
from app.search import vector_store
from app.utils.temporal import resolve_date_phrase


class GCalAgent(BaseAgent):
    service_name = "gcal"

    def __init__(self, session: AsyncSession, user: User, embeddings: EmbeddingProvider, client):
        self._session = session
        self._user = user
        self._embeddings = embeddings
        self._client = client

    async def search(self, query: str, filters: dict | None = None, limit: int = 10) -> list[SearchResult]:
        filters = filters or {}
        attendee = filters.get("attendee") or _first(filters.get("email_addresses"))
        since, until = resolve_date_phrase(filters.get("date_phrase"), datetime.now(UTC), self._user.timezone)
        query_text = "" if query in ("*", None) else query
        embedding = await self._embeddings.embed_one(query_text or "calendar event")
        rows = await vector_store.search_gcal(
            self._session, self._user.id, query_text, embedding,
            attendee=attendee, since=since, until=until, limit=limit,
        )
        return [self._to_search_result(r) for r in rows]

    def _to_search_result(self, row: dict) -> SearchResult:
        return SearchResult(
            id=row["event_id"],
            service="gcal",
            title=row.get("title") or "(untitled event)",
            snippet=(row.get("description") or "")[:280],
            score=float(row.get("fused_score") or 0.0),
            metadata={
                "attendees": row.get("attendees") or [],
                "organizer": row.get("organizer"),
                "location": row.get("location"),
                "status": row.get("status"),
                "end_time": row.get("end_time").isoformat() if row.get("end_time") else None,
            },
            timestamp=row.get("start_time"),
        )

    async def get_context(self, item_id: str) -> dict:
        try:
            return await self._client.get_event(item_id)
        except KeyError as e:
            raise AgentError(str(e), retryable=False) from e

    async def execute(self, action: str, params: dict) -> dict:
        if action == "create_event":
            return await self._client.create_event(
                params["summary"], params.get("description", ""), params["start"], params["end"],
                params.get("attendees"), params.get("location"),
            )
        if action == "update_event":
            event_id = self._resolve_event_id(params)
            return await self._client.update_event(event_id, params.get("patch", {}))
        if action == "delete_event":
            return await self._client.delete_event(self._resolve_event_id(params))
        raise AgentError(f"unknown gcal action: {action}", retryable=False)

    @staticmethod
    def _resolve_event_id(params: dict) -> str:
        event_ref = params.get("event_ref")
        event_id = getattr(event_ref, "id", None) or params.get("event_id")
        if not event_id:
            raise AgentError("no target event resolved for this action", retryable=False)
        return event_id


def _first(values: list | None):
    return values[0] if values else None
