"""Parallel DAG execution engine — the core of the "Service Orchestrator" box in the
architecture diagram. Runs each topological layer of the plan concurrently with asyncio.gather,
propagates dependency failures without crashing the whole run (a Gmail failure doesn't take down
a parallel Calendar search), enforces a per-node timeout, and blocks all write operations
whenever the classifier flagged the query as ambiguous.
"""
import asyncio
import time
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.db.models import AuditLog
from app.intent.schemas import Intent
from app.orchestrator import compute
from app.orchestrator.types import ExecutionReport, NodeResult, NodeStatus
from app.planner.dag import ExecutionDAG, PlanNode
from app.utils.serialization import json_safe

logger = structlog.get_logger(__name__)

NODE_TIMEOUT_SECONDS = 15.0


class ServiceOrchestrator:
    def __init__(
        self,
        agents: dict[str, BaseAgent],
        session: AsyncSession,
        user_id: UUID,
        conversation_history: list[dict] | None = None,
    ):
        self._agents = agents
        self._session = session
        self._user_id = user_id
        self._conversation_history = conversation_history or []

    async def run(
        self,
        dag: ExecutionDAG,
        intent: Intent,
        on_node_complete=None,  # optional async callback(node: PlanNode, result: NodeResult) -> None, for WS streaming
    ) -> ExecutionReport:
        report = ExecutionReport()
        for layer in dag.topological_layers():
            outcomes = await asyncio.gather(*(self._run_node(node, intent, report) for node in layer))
            for node, result in zip(layer, outcomes, strict=True):
                report.results[node.id] = result
                if result.status == NodeStatus.ok and node.operation == "execute":
                    report.actions_taken.append(node.description or f"{node.agent}.{node.action}")
                if on_node_complete is not None:
                    await on_node_complete(node, result)
        return report

    async def _run_node(self, node: PlanNode, intent: Intent, report: ExecutionReport) -> NodeResult:
        blocking_dep = self._blocked_by_dependency(node, report)
        if blocking_dep:
            return NodeResult(node.id, NodeStatus.skipped, error=f"dependency '{blocking_dep}' did not succeed")

        if node.is_write and intent.needs_clarification:
            return NodeResult(node.id, NodeStatus.skipped, error="blocked: query needs clarification before a write")

        start = time.monotonic()
        try:
            params = node.build_params(intent, report.results)
            if node.agent == "compute":
                params.setdefault("conversation_history", self._conversation_history)
            data = await asyncio.wait_for(self._dispatch(node, params), timeout=NODE_TIMEOUT_SECONDS)
            duration_ms = (time.monotonic() - start) * 1000
            if node.is_write:
                await self._audit(node, params, "success", None)
            logger.info("node_ok", node_id=node.id, agent=node.agent, duration_ms=round(duration_ms, 1))
            return NodeResult(node.id, NodeStatus.ok, data=data, duration_ms=duration_ms)
        except TimeoutError:
            duration_ms = (time.monotonic() - start) * 1000
            logger.warning("node_timeout", node_id=node.id, agent=node.agent)
            if node.is_write:
                await self._audit(node, {}, "error", "timeout")
            return NodeResult(node.id, NodeStatus.error, error="timed out", duration_ms=duration_ms)
        except Exception as exc:  # noqa: BLE001 - a failing node must not crash the whole DAG
            duration_ms = (time.monotonic() - start) * 1000
            logger.warning("node_failed", node_id=node.id, agent=node.agent, error=str(exc))
            if node.is_write:
                await self._audit(node, {}, "error", str(exc))
            return NodeResult(node.id, NodeStatus.error, error=str(exc), duration_ms=duration_ms)

    def _blocked_by_dependency(self, node: PlanNode, report: ExecutionReport) -> str | None:
        for dep in node.depends_on:
            dep_result = report.results.get(dep)
            if dep_result is None:
                return dep  # should not happen given topological ordering, but fail safe
            if dep_result.status != NodeStatus.ok and not node.optional:
                return dep
        return None

    async def _dispatch(self, node: PlanNode, params: dict):
        if node.agent == "compute":
            handler = compute.HANDLERS.get(node.id)
            if handler is None:
                raise ValueError(f"no compute handler registered for node '{node.id}'")
            return await handler(params)

        agent = self._agents.get(node.agent)
        if agent is None:
            raise ValueError(f"no agent registered for service '{node.agent}'")

        if node.operation == "search":
            return await agent.search(params.get("query", ""), params.get("filters"), params.get("limit", 10))
        if node.operation == "get_context":
            item_id = params.get("item_id")
            if not item_id:
                return None
            return await agent.get_context(item_id)
        if node.operation == "execute":
            return await agent.execute(node.action, params)
        raise ValueError(f"unknown node operation: {node.operation}")

    async def _audit(self, node: PlanNode, params: dict, result: str, error: str | None) -> None:
        self._session.add(AuditLog(
            user_id=self._user_id,
            service=node.agent,
            operation=node.action or node.operation,
            payload=json_safe(params),
            result=result,
            error_detail=error,
        ))
        await self._session.commit()


def build_entities_referenced(report: ExecutionReport) -> dict:
    """Collect every search-hit surfaced this turn, grouped by service, for storage on the
    Conversation row so a later "that email" can be resolved against it (see orchestrator.compute
    .resolve_reference)."""
    referenced: dict[str, list[dict]] = {}
    for result in report.results.values():
        if result.status != NodeStatus.ok or not isinstance(result.data, list):
            continue
        for item in result.data:
            if hasattr(item, "to_dict"):
                referenced.setdefault(item.service, []).append(item.to_dict())
    return referenced
