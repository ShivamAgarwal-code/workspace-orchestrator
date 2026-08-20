from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentError, BaseAgent, SearchResult
from app.db.models import User
from app.llm.base import EmbeddingProvider
from app.search import vector_store
from app.utils.temporal import resolve_date_phrase

_MIME_KEYWORDS = {
    "pdf": "application/pdf",
    "doc": "application/vnd.google-apps.document",
    "document": "application/vnd.google-apps.document",
    "sheet": "application/vnd.google-apps.spreadsheet",
    "spreadsheet": "application/vnd.google-apps.spreadsheet",
    "slide": "application/vnd.google-apps.presentation",
    "presentation": "application/vnd.google-apps.presentation",
    "image": "image/",
    "photo": "image/",
}


class DriveAgent(BaseAgent):
    service_name = "gdrive"

    def __init__(self, session: AsyncSession, user: User, embeddings: EmbeddingProvider, client):
        self._session = session
        self._user = user
        self._embeddings = embeddings
        self._client = client

    async def search(self, query: str, filters: dict | None = None, limit: int = 10) -> list[SearchResult]:
        filters = filters or {}
        mime_type = filters.get("mime_type") or _infer_mime_type(filters.get("file_type"), query)
        since, until = resolve_date_phrase(filters.get("date_phrase"), datetime.now(UTC), self._user.timezone)
        embedding = await self._embeddings.embed_one(query or "")
        rows = await vector_store.search_gdrive(
            self._session, self._user.id, query, embedding,
            mime_type=mime_type if mime_type and not mime_type.endswith("/") else None,
            since=since, until=until, limit=limit,
        )
        if mime_type and mime_type.endswith("/"):  # broad prefix match (e.g. "image/") done client-side
            rows = [r for r in rows if (r.get("mime_type") or "").startswith(mime_type)]
        return [self._to_search_result(r) for r in rows]

    def _to_search_result(self, row: dict) -> SearchResult:
        metadata = {
            "mime_type": row.get("mime_type"),
            "owners": row.get("owners"),
            "web_view_link": row.get("web_view_link"),
            "parent_folder_id": row.get("parent_folder_id"),
        }
        metadata.update(row.get("extra_metadata") or {})
        return SearchResult(
            id=row["file_id"],
            service="gdrive",
            title=row.get("name") or "(untitled)",
            snippet=(row.get("content_preview") or "")[:280],
            score=float(row.get("fused_score") or 0.0),
            metadata=metadata,
            timestamp=row.get("modified_at"),
        )

    async def get_context(self, item_id: str) -> dict:
        try:
            return await self._client.get_file(item_id)
        except KeyError as e:
            raise AgentError(str(e), retryable=False) from e

    async def execute(self, action: str, params: dict) -> dict:
        if action == "share_file":
            return await self._client.share_file(params["file_id"], params["email"], params.get("role", "reader"))
        if action == "create_folder":
            return await self._client.create_folder(params["name"], params.get("parent_id"))
        if action == "move_file":
            return await self._client.move_file(params["file_id"], params["new_parent_id"])
        raise AgentError(f"unknown gdrive action: {action}", retryable=False)


def _infer_mime_type(file_type: str | None, query: str) -> str | None:
    if file_type and file_type.lower() in _MIME_KEYWORDS:
        return _MIME_KEYWORDS[file_type.lower()]
    q = (query or "").lower()
    for keyword, mime in _MIME_KEYWORDS.items():
        if keyword in q:
            return mime
    return None
