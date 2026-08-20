import pytest

from app.planner.dag import ExecutionDAG, PlanNode


def _node(node_id: str, depends_on: list[str] | None = None) -> PlanNode:
    return PlanNode(
        id=node_id, agent="gmail", operation="search",
        build_params=lambda i, r: {}, depends_on=depends_on or [],
    )


def test_independent_nodes_form_single_parallel_layer():
    dag = ExecutionDAG([_node("a"), _node("b"), _node("c")])
    layers = dag.topological_layers()
    assert len(layers) == 1
    assert {n.id for n in layers[0]} == {"a", "b", "c"}


def test_sequential_dependency_creates_separate_layers():
    dag = ExecutionDAG([_node("a"), _node("b", depends_on=["a"])])
    layers = dag.topological_layers()
    assert [n.id for n in layers[0]] == ["a"]
    assert [n.id for n in layers[1]] == ["b"]


def test_mixed_parallel_and_sequential():
    # a, b run in parallel; c depends on both -> matches "search Gmail + Calendar in parallel,
    # then draft an email that needs both results" from the cancel_flight template.
    dag = ExecutionDAG([_node("a"), _node("b"), _node("c", depends_on=["a", "b"])])
    layers = dag.topological_layers()
    assert {n.id for n in layers[0]} == {"a", "b"}
    assert [n.id for n in layers[1]] == ["c"]


def test_diamond_dependency():
    dag = ExecutionDAG([
        _node("a"),
        _node("b", depends_on=["a"]),
        _node("c", depends_on=["a"]),
        _node("d", depends_on=["b", "c"]),
    ])
    layers = dag.topological_layers()
    assert [n.id for n in layers[0]] == ["a"]
    assert {n.id for n in layers[1]} == {"b", "c"}
    assert [n.id for n in layers[2]] == ["d"]


def test_cycle_detection_raises():
    dag = ExecutionDAG([_node("a", depends_on=["b"]), _node("b", depends_on=["a"])])
    with pytest.raises(ValueError, match="cycle"):
        dag.topological_layers()


def test_unknown_dependency_raises():
    dag = ExecutionDAG([_node("a", depends_on=["missing"])])
    with pytest.raises(ValueError, match="unknown node"):
        dag.topological_layers()


def test_duplicate_node_id_raises():
    dag = ExecutionDAG()
    dag.add_node(_node("a"))
    with pytest.raises(ValueError, match="duplicate"):
        dag.add_node(_node("a"))
