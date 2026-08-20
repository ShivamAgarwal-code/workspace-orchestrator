"""Runtime result types shared between the planner (which references them for typing param
builders) and the orchestrator (which produces/consumes them). Kept dependency-free so both
`app.planner` and `app.orchestrator` can import from here without a circular import.
"""
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class NodeStatus(StrEnum):
    ok = "ok"
    error = "error"
    skipped = "skipped"  # dependency failed, or blocked by needs_clarification policy


@dataclass
class NodeResult:
    node_id: str
    status: NodeStatus
    data: Any = None
    error: str | None = None
    duration_ms: float = 0.0


@dataclass
class ExecutionReport:
    results: dict[str, NodeResult] = field(default_factory=dict)
    actions_taken: list[str] = field(default_factory=list)

    def get(self, node_id: str) -> NodeResult | None:
        return self.results.get(node_id)

    def ok(self, node_id: str) -> bool:
        r = self.results.get(node_id)
        return r is not None and r.status == NodeStatus.ok
