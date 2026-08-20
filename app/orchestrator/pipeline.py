"""The full query pipeline (rate limit -> history -> classify -> plan -> orchestrate ->
synthesize -> persist), factored out of the REST handler so the WebSocket endpoint can drive the
exact same logic while additionally streaming per-node progress events.
"""
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import build_agents
from app.cache.rate_limiter import check_and_increment
from app.db.models import User
from app.intent.classifier import IntentClassifier
from app.intent.schemas import Intent
from app.llm.factory import get_llm_provider
from app.orchestrator.context import get_recent_conversations, save_conversation
from app.orchestrator.orchestrator import ServiceOrchestrator, build_entities_referenced
from app.orchestrator.reporting import split_results_and_errors
from app.orchestrator.types import NodeResult
from app.planner.dag import PlanNode
from app.planner.query_planner import QueryPlanner
from app.synthesizer.response_synthesizer import ResponseSynthesizer

logger = structlog.get_logger(__name__)

NodeCallback = Callable[[PlanNode, NodeResult], Awaitable[None]]


@dataclass
class PipelineResult:
    response: str
    conversation_id: str
    intent: Intent
    actions_taken: list[str]
    results: dict[str, list[dict]]
    errors: dict[str, str]
    timing_ms: dict[str, float]


async def run_query_pipeline(
    session: AsyncSession,
    user: User,
    query: str,
    on_node_complete: NodeCallback | None = None,
) -> PipelineResult:
    await check_and_increment(str(user.id))

    timing: dict[str, float] = {}
    t_start = time.monotonic()

    history = await get_recent_conversations(session, user.id)

    llm = get_llm_provider()
    t = time.monotonic()
    intent = await IntentClassifier(llm).classify(
        query, conversation_history=history, now_iso=datetime.now(UTC).isoformat(), timezone=user.timezone,
    )
    timing["classify_ms"] = (time.monotonic() - t) * 1000

    dag = QueryPlanner().build_plan(intent)
    agents = await build_agents(session, user)
    orchestrator = ServiceOrchestrator(agents, session, user.id, conversation_history=history)

    t = time.monotonic()
    report = await orchestrator.run(dag, intent, on_node_complete=on_node_complete)
    timing["execute_ms"] = (time.monotonic() - t) * 1000

    t = time.monotonic()
    response_text = await ResponseSynthesizer(llm).synthesize(query, intent, dag, report)
    timing["synthesize_ms"] = (time.monotonic() - t) * 1000

    entities_referenced = build_entities_referenced(report)
    conversation = await save_conversation(
        session, user.id, query, intent.model_dump(), response_text, report.actions_taken, entities_referenced,
    )

    results, errors = split_results_and_errors(dag, report)
    timing["total_ms"] = (time.monotonic() - t_start) * 1000

    logger.info(
        "query_completed", user_id=str(user.id), intent=intent.intent,
        total_ms=round(timing["total_ms"], 1), needs_clarification=intent.needs_clarification,
    )

    return PipelineResult(
        response=response_text,
        conversation_id=str(conversation.id),
        intent=intent,
        actions_taken=report.actions_taken,
        results=results,
        errors=errors,
        timing_ms={k: round(v, 1) for k, v in timing.items()},
    )
