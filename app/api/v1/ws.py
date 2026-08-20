"""Bonus: WebSocket endpoint streaming per-node orchestration progress in real time (a client can
show "searching Gmail... done (120ms)", "searching Calendar... done (95ms)" live instead of
waiting for the whole DAG to finish), then a final synthesized-response message. Drives the exact
same `run_query_pipeline` as the REST endpoint.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.rate_limiter import RateLimitExceeded
from app.constants import DEMO_USER_EMAIL, DEMO_USER_ID
from app.db.base import get_db
from app.db.models import User
from app.orchestrator.pipeline import run_query_pipeline
from app.utils.serialization import json_safe

router = APIRouter()


async def _resolve_ws_user(session: AsyncSession, user_id: UUID | None) -> User:
    target_id = user_id or DEMO_USER_ID
    user = await session.get(User, target_id)
    if user is None and target_id == DEMO_USER_ID:
        user = User(id=DEMO_USER_ID, email=DEMO_USER_EMAIL, timezone="UTC")
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


@router.websocket("/api/v1/ws/query")
async def query_ws(
    websocket: WebSocket,
    user_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
):
    await websocket.accept()
    try:
        while True:
            payload = await websocket.receive_json()
            query = (payload.get("query") or "").strip()
            if not query:
                await websocket.send_json({"type": "error", "message": "missing 'query'"})
                continue

            user = await _resolve_ws_user(session, user_id)

            async def on_node_complete(node, result, ws=websocket):
                await ws.send_json({
                    "type": "node_complete",
                    "node_id": node.id,
                    "agent": node.agent,
                    "description": node.description,
                    "status": str(result.status),
                    "duration_ms": round(result.duration_ms, 1),
                    "error": result.error,
                })

            try:
                result = await run_query_pipeline(session, user, query, on_node_complete=on_node_complete)
            except RateLimitExceeded as exc:
                await websocket.send_json({
                    "type": "error", "message": str(exc), "retry_after_seconds": exc.retry_after_seconds,
                })
                continue

            await websocket.send_json({
                "type": "final",
                "response": result.response,
                "conversation_id": result.conversation_id,
                "intent": result.intent.intent,
                "needs_clarification": result.needs_clarification,
                "clarification_question": result.clarification_question,
                "actions_taken": result.actions_taken,
                "results": json_safe(result.results),
                "errors": result.errors,
                "timing_ms": result.timing_ms,
            })
    except WebSocketDisconnect:
        return
