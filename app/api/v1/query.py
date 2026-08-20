"""POST /api/v1/query — the single entry point that runs the full pipeline: intent classification
-> query planning -> parallel orchestration -> hybrid search / execution -> response synthesis.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.rate_limiter import RateLimitExceeded
from app.db.base import get_db
from app.db.models import User
from app.dependencies import get_current_user
from app.orchestrator.pipeline import run_query_pipeline
from app.schemas.query import QueryRequest, QueryResponse

router = APIRouter(prefix="/api/v1", tags=["query"])


@router.post("/query", response_model=QueryResponse)
async def run_query(
    body: QueryRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> QueryResponse:
    try:
        result = await run_query_pipeline(session, user, body.query)
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429, detail=str(exc), headers={"Retry-After": str(exc.retry_after_seconds)}
        ) from exc

    return QueryResponse(
        response=result.response,
        conversation_id=result.conversation_id,
        intent=result.intent.intent,
        services_used=result.intent.services,
        actions_taken=result.actions_taken,
        needs_clarification=result.intent.needs_clarification,
        clarification_question=result.intent.clarification_question,
        results=result.results,
        errors=result.errors,
        timing_ms=result.timing_ms,
    )
