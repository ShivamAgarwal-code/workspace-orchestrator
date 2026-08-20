from uuid import UUID

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    conversation_id: UUID | None = None


class QueryResponse(BaseModel):
    response: str
    conversation_id: UUID
    intent: str
    services_used: list[str]
    actions_taken: list[str]
    needs_clarification: bool
    clarification_question: str | None = None
    results: dict[str, list[dict]]
    errors: dict[str, str]
    timing_ms: dict[str, float]
