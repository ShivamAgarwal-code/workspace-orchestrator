from app.intent.schemas import Intent
from app.planner.query_planner import QueryPlanner


def _intent(**overrides) -> Intent:
    defaults = dict(services=[], intent="general_query", entities={}, steps=[], raw_query="test query")
    defaults.update(overrides)
    return Intent(**defaults)


def test_cancel_flight_has_parallel_search_then_sequential_draft():
    dag = QueryPlanner().build_plan(_intent(intent="cancel_flight", entities={"airline": "Turkish Airlines"}))
    layers = dag.topological_layers()
    assert {n.id for n in layers[0]} == {"find_booking_email", "find_calendar_event"}
    assert [n.id for n in layers[1]] == ["draft_cancellation_email"]
    draft_node = dag.nodes["draft_cancellation_email"]
    assert draft_node.is_write is True
    assert draft_node.action == "draft_email"


def test_prepare_for_meeting_has_sequential_email_search_after_calendar():
    dag = QueryPlanner().build_plan(_intent(intent="prepare_for_meeting", entities={"company": "Acme Corp"}))
    layers = dag.topological_layers()
    first_layer_ids = {n.id for n in layers[0]}
    # calendar + drive search run in parallel; gmail search depends on the calendar result
    # (needs the attendee list), matching "Find calendar event -> search emails -> pull docs".
    assert "find_meeting_event" in first_layer_ids
    assert "find_drive_docs" in first_layer_ids
    assert "find_participant_emails" not in first_layer_ids
    all_ids = {n.id for layer in layers for n in layer}
    assert "find_participant_emails" in all_ids


def test_find_conflicts_has_compute_node_depending_on_both_searches():
    dag = QueryPlanner().build_plan(_intent(intent="find_conflicts"))
    conflicts_node = dag.nodes["detect_conflicts"]
    assert conflicts_node.agent == "compute"
    assert set(conflicts_node.depends_on) == {"search_events", "search_oof_doc"}


def test_reschedule_event_write_node_is_flagged():
    dag = QueryPlanner().build_plan(_intent(intent="reschedule_event", entities={"person": "John"}))
    assert dag.nodes["update_event"].is_write is True
    assert dag.nodes["update_event"].depends_on == ["find_candidate_events"]


def test_single_service_search_templates():
    for intent_name, expected_agent in [
        ("search_calendar", "gcal"),
        ("search_email", "gmail"),
        ("search_drive", "gdrive"),
    ]:
        dag = QueryPlanner().build_plan(_intent(intent=intent_name))
        assert len(dag.nodes) == 1
        node = next(iter(dag.nodes.values()))
        assert node.agent == expected_agent
        assert node.operation == "search"


def test_generic_fallback_fans_out_over_declared_services():
    dag = QueryPlanner().build_plan(_intent(intent="totally_unknown_intent", services=["gmail", "gdrive"]))
    assert {n.agent for n in dag.nodes.values()} == {"gmail", "gdrive"}
    layers = dag.topological_layers()
    assert len(layers) == 1  # all independent, fully parallel


def test_generic_fallback_passes_entities_as_filters():
    intent = _intent(intent="totally_unknown_intent", services=["gcal"], entities={"date_phrase": "next week"})
    dag = QueryPlanner().build_plan(intent)
    node = dag.nodes["search_gcal"]
    params = node.build_params(intent, {})
    assert params["filters"] == {"date_phrase": "next week"}


def test_reference_lookup_get_context_is_optional_and_depends_on_resolve():
    dag = QueryPlanner().build_plan(_intent(intent="reference_lookup", raw_query="that email about the proposal"))
    ctx_node = dag.nodes["get_referenced_context"]
    assert ctx_node.optional is True
    assert ctx_node.depends_on == ["resolve_reference"]
