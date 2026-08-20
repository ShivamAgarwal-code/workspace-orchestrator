import uuid

import pytest

import app.orchestrator.orchestrator as orchestrator_module
from app.agents.base import BaseAgent
from app.intent.schemas import Intent
from app.orchestrator.orchestrator import ServiceOrchestrator
from app.orchestrator.types import NodeStatus
from app.planner.dag import ExecutionDAG, PlanNode


class FakeSession:
    def add(self, obj):
        pass

    async def commit(self):
        pass


class FakeAgent(BaseAgent):
    def __init__(self, service_name="gmail", search_result=None, raise_on_search=False):
        self.service_name = service_name
        self._search_result = search_result or []
        self._raise_on_search = raise_on_search
        self.execute_called_with = None

    async def search(self, query, filters=None, limit=10):
        if self._raise_on_search:
            raise RuntimeError("simulated Gmail API failure")
        return self._search_result

    async def execute(self, action, params):
        self.execute_called_with = (action, params)
        return {"status": "done"}

    async def get_context(self, item_id):
        return {"id": item_id}


def _intent(needs_clarification=False) -> Intent:
    return Intent(services=[], intent="test", entities={}, steps=[], needs_clarification=needs_clarification)


async def test_independent_node_failure_does_not_block_sibling():
    failing_gmail = FakeAgent("gmail", raise_on_search=True)
    working_gcal = FakeAgent("gcal", search_result=["event1"])
    dag = ExecutionDAG([
        PlanNode(id="a", agent="gmail", operation="search", build_params=lambda i, r: {}),
        PlanNode(id="b", agent="gcal", operation="search", build_params=lambda i, r: {}),
    ])
    orch = ServiceOrchestrator({"gmail": failing_gmail, "gcal": working_gcal}, FakeSession(), uuid.uuid4())

    report = await orch.run(dag, _intent())

    assert report.results["a"].status == NodeStatus.error
    assert "simulated Gmail API failure" in report.results["a"].error
    assert report.results["b"].status == NodeStatus.ok
    assert report.results["b"].data == ["event1"]


async def test_dependent_node_is_skipped_when_dependency_fails():
    failing_agent = FakeAgent("gmail", raise_on_search=True)
    dag = ExecutionDAG([
        PlanNode(id="a", agent="gmail", operation="search", build_params=lambda i, r: {}),
        PlanNode(id="b", agent="gmail", operation="get_context", build_params=lambda i, r: {"item_id": "x"}, depends_on=["a"]),
    ])
    orch = ServiceOrchestrator({"gmail": failing_agent}, FakeSession(), uuid.uuid4())

    report = await orch.run(dag, _intent())

    assert report.results["a"].status == NodeStatus.error
    assert report.results["b"].status == NodeStatus.skipped


async def test_optional_dependent_node_still_runs_after_dependency_failure():
    failing_agent = FakeAgent("gmail", raise_on_search=True)
    dag = ExecutionDAG([
        PlanNode(id="a", agent="gmail", operation="search", build_params=lambda i, r: {}),
        PlanNode(
            id="b", agent="gmail", operation="get_context", build_params=lambda i, r: {"item_id": "x"},
            depends_on=["a"], optional=True,
        ),
    ])
    orch = ServiceOrchestrator({"gmail": failing_agent}, FakeSession(), uuid.uuid4())

    report = await orch.run(dag, _intent())

    assert report.results["a"].status == NodeStatus.error
    assert report.results["b"].status == NodeStatus.ok


async def test_write_node_blocked_when_intent_needs_clarification():
    agent = FakeAgent("gcal")
    dag = ExecutionDAG([
        PlanNode(
            id="update", agent="gcal", operation="execute", action="update_event",
            build_params=lambda i, r: {"event_id": "evt1"}, is_write=True,
        ),
    ])
    orch = ServiceOrchestrator({"gcal": agent}, FakeSession(), uuid.uuid4())

    report = await orch.run(dag, _intent(needs_clarification=True))

    assert report.results["update"].status == NodeStatus.skipped
    assert agent.execute_called_with is None  # the write must never actually happen


async def test_write_node_runs_and_is_recorded_in_actions_taken_when_not_ambiguous():
    agent = FakeAgent("gmail")
    dag = ExecutionDAG([
        PlanNode(
            id="draft", agent="gmail", operation="execute", action="draft_email",
            build_params=lambda i, r: {"to": "a@b.com", "subject": "s", "body": "b"},
            is_write=True, description="Draft the cancellation email",
        ),
    ])
    orch = ServiceOrchestrator({"gmail": agent}, FakeSession(), uuid.uuid4())

    report = await orch.run(dag, _intent(needs_clarification=False))

    assert report.results["draft"].status == NodeStatus.ok
    assert agent.execute_called_with[0] == "draft_email"
    assert "Draft the cancellation email" in report.actions_taken


async def test_node_timeout_is_reported_as_error(monkeypatch):
    import asyncio

    monkeypatch.setattr(orchestrator_module, "NODE_TIMEOUT_SECONDS", 0.05)

    class SlowAgent(FakeAgent):
        async def search(self, query, filters=None, limit=10):
            await asyncio.sleep(1)
            return []

    dag = ExecutionDAG([PlanNode(id="slow", agent="gmail", operation="search", build_params=lambda i, r: {})])
    orch = ServiceOrchestrator({"gmail": SlowAgent("gmail")}, FakeSession(), uuid.uuid4())

    report = await orch.run(dag, _intent())

    assert report.results["slow"].status == NodeStatus.error
    assert "timed out" in report.results["slow"].error


async def test_on_node_complete_callback_invoked_per_node():
    calls = []

    async def callback(node, result):
        calls.append((node.id, result.status))

    dag = ExecutionDAG([
        PlanNode(id="a", agent="gmail", operation="search", build_params=lambda i, r: {}),
        PlanNode(id="b", agent="gcal", operation="search", build_params=lambda i, r: {}, depends_on=[]),
    ])
    orch = ServiceOrchestrator({"gmail": FakeAgent("gmail"), "gcal": FakeAgent("gcal")}, FakeSession(), uuid.uuid4())

    await orch.run(dag, _intent(), on_node_complete=callback)

    assert {c[0] for c in calls} == {"a", "b"}
    assert all(status == NodeStatus.ok for _, status in calls)


async def test_unknown_agent_reference_is_a_node_error_not_a_crash():
    dag = ExecutionDAG([PlanNode(id="a", agent="nonexistent_service", operation="search", build_params=lambda i, r: {})])
    orch = ServiceOrchestrator({}, FakeSession(), uuid.uuid4())

    report = await orch.run(dag, _intent())

    assert report.results["a"].status == NodeStatus.error
