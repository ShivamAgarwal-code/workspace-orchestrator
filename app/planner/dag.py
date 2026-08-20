"""Execution DAG data structures: plan nodes + dependency-aware topological batching.

Built from scratch (no workflow/agent framework) — `topological_layers()` implements Kahn's
algorithm to group nodes into batches that can run concurrently, which is exactly what the
orchestrator needs to fan work out with `asyncio.gather` per batch while respecting
dependencies between batches.
"""
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.intent.schemas import Intent
    from app.orchestrator.types import NodeResult

# (intent, {node_id: NodeResult}) -> params dict for this node's agent operation
ParamBuilder = Callable[["Intent", dict[str, "NodeResult"]], dict]


@dataclass
class PlanNode:
    id: str
    agent: str  # "gmail" | "gcal" | "gdrive" | "compute"
    operation: str  # "search" | "execute" | "get_context" | "compute"
    build_params: ParamBuilder
    depends_on: list[str] = field(default_factory=list)
    action: str | None = None  # required when operation == "execute", e.g. "draft_email"
    optional: bool = False  # if True, this node's failure does not block dependents
    is_write: bool = False  # write ops are blocked whenever intent.needs_clarification is True
    description: str = ""


class ExecutionDAG:
    def __init__(self, nodes: list[PlanNode] | None = None):
        self.nodes: dict[str, PlanNode] = {}
        for n in nodes or []:
            self.add_node(n)

    def add_node(self, node: PlanNode) -> None:
        if node.id in self.nodes:
            raise ValueError(f"duplicate node id: {node.id}")
        self.nodes[node.id] = node

    def topological_layers(self) -> list[list[PlanNode]]:
        """Kahn's algorithm, batched: each returned layer can execute in parallel."""
        in_degree = {nid: 0 for nid in self.nodes}
        dependents: dict[str, list[str]] = {nid: [] for nid in self.nodes}
        for node in self.nodes.values():
            for dep in node.depends_on:
                if dep not in self.nodes:
                    raise ValueError(f"node {node.id} depends on unknown node {dep}")
                in_degree[node.id] += 1
                dependents[dep].append(node.id)

        layers: list[list[PlanNode]] = []
        remaining = dict(in_degree)
        ready = sorted([nid for nid, deg in remaining.items() if deg == 0])
        while ready:
            layer = [self.nodes[nid] for nid in ready]
            layers.append(layer)
            next_ready: list[str] = []
            for nid in ready:
                del remaining[nid]
                for dep_nid in dependents[nid]:
                    remaining[dep_nid] -= 1
                    if remaining[dep_nid] == 0:
                        next_ready.append(dep_nid)
            ready = sorted(next_ready)

        if remaining:
            raise ValueError(f"cycle detected in execution DAG involving nodes: {sorted(remaining)}")
        return layers

    def __len__(self) -> int:
        return len(self.nodes)
