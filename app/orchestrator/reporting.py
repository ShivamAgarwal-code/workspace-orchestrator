"""Shared post-processing of an ExecutionReport into the (results-by-service, errors-by-service)
shape both the API response and the response synthesizer's LLM context need."""
from app.intent.schemas import Intent
from app.orchestrator.types import ExecutionReport, NodeStatus
from app.planner.dag import ExecutionDAG

_UNRESOLVED_REFERENCE_QUESTION = "Which email are you referring to? Could you share a keyword or the sender?"


def split_results_and_errors(dag: ExecutionDAG, report: ExecutionReport) -> tuple[dict[str, list[dict]], dict[str, str]]:
    results: dict[str, list[dict]] = {"gmail": [], "gcal": [], "gdrive": []}
    errors: dict[str, str] = {}
    for node_id, result in report.results.items():
        node = dag.nodes.get(node_id)
        service = node.agent if node else "unknown"
        if result.status == NodeStatus.ok and isinstance(result.data, list):
            for item in result.data:
                if hasattr(item, "to_dict") and item.service in results:
                    results[item.service].append(item.to_dict())
        elif result.status == NodeStatus.error and service in results:
            errors[service] = result.error or "unknown error"
    return results, errors


def resolve_final_clarification(intent: Intent, report: ExecutionReport) -> tuple[bool, str | None]:
    """The classifier's needs_clarification is a best-effort guess made before any DAG node ran.
    For reference_lookup ("that email...") it can't actually know whether the reference resolves
    until the resolve_reference compute node checks conversation history — so that node's result,
    once available, overrides the classifier's guess. Used by both the API response fields and
    the synthesizer's LLM context so they never disagree."""
    needs_clarification = intent.needs_clarification
    clarification_question = intent.clarification_question

    resolve_ref_result = report.results.get("resolve_reference")
    if resolve_ref_result and resolve_ref_result.status == NodeStatus.ok and isinstance(resolve_ref_result.data, dict):
        if not resolve_ref_result.data.get("resolved"):
            needs_clarification = True
            clarification_question = clarification_question or _UNRESOLVED_REFERENCE_QUESTION
        else:
            needs_clarification = False
            clarification_question = None

    return needs_clarification, clarification_question
