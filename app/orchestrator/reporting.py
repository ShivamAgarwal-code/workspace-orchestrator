"""Shared post-processing of an ExecutionReport into the (results-by-service, errors-by-service)
shape both the API response and the response synthesizer's LLM context need."""
from app.orchestrator.types import ExecutionReport, NodeStatus
from app.planner.dag import ExecutionDAG


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
