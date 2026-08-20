from datetime import UTC, datetime

from app.agents.base import SearchResult
from app.orchestrator import compute
from app.orchestrator.types import NodeResult, NodeStatus


def _search_result(id_, service, title, snippet="", metadata=None, timestamp=None) -> SearchResult:
    return SearchResult(id=id_, service=service, title=title, snippet=snippet, score=0.5, metadata=metadata or {}, timestamp=timestamp)


async def test_detect_conflicts_finds_event_inside_ooo_window():
    ooo_doc = _search_result(
        "file1", "gdrive", "Team OOO Schedule",
        metadata={"ooo_start": "2026-08-24T00:00:00+00:00", "ooo_end": "2026-08-26T23:59:00+00:00"},
    )
    conflicting_event = _search_result(
        "evt1", "gcal", "Product Sync", timestamp=datetime(2026, 8, 25, 9, 30, tzinfo=UTC),
    )
    clean_event = _search_result(
        "evt2", "gcal", "Dentist", timestamp=datetime(2026, 8, 30, 8, 0, tzinfo=UTC),
    )

    result = await compute.detect_conflicts({
        "events": NodeResult("search_events", NodeStatus.ok, data=[conflicting_event, clean_event]),
        "doc": NodeResult("search_oof_doc", NodeStatus.ok, data=[ooo_doc]),
    })

    assert result["checked_events"] == 2
    assert result["windows_checked"] == 1
    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["conflicting_document"] == "Team OOO Schedule"


async def test_detect_conflicts_with_no_ooo_metadata_finds_nothing():
    doc_without_dates = _search_result("file1", "gdrive", "Random doc", metadata={})
    event = _search_result("evt1", "gcal", "Meeting", timestamp=datetime(2026, 8, 25, 9, 0, tzinfo=UTC))

    result = await compute.detect_conflicts({
        "events": NodeResult("search_events", NodeStatus.ok, data=[event]),
        "doc": NodeResult("search_oof_doc", NodeStatus.ok, data=[doc_without_dates]),
    })

    assert result["conflicts"] == []


async def test_detect_conflicts_handles_upstream_failure_gracefully():
    result = await compute.detect_conflicts({
        "events": NodeResult("search_events", NodeStatus.error, error="boom"),
        "doc": NodeResult("search_oof_doc", NodeStatus.ok, data=[]),
    })
    assert result["conflicts"] == []
    assert result["checked_events"] == 0


async def test_resolve_reference_matches_via_keyword():
    history = [{
        "query": "Find emails about the proposal",
        "entities_referenced": {
            "gmail": [{"id": "msg_acme_proposal", "title": "The Proposal - Draft v2", "snippet": "revised pricing"}],
        },
    }]
    result = await compute.resolve_reference({"query": "That email about the proposal", "conversation_history": history})
    assert result == {"resolved": True, "item_id": "msg_acme_proposal", "service": "gmail"}


async def test_resolve_reference_unresolved_with_no_history():
    result = await compute.resolve_reference({"query": "That email about the proposal", "conversation_history": []})
    assert result["resolved"] is False
    assert result["item_id"] is None


async def test_resolve_reference_ignores_punctuation_in_keywords():
    history = [{
        "query": "Find emails about the proposal",
        "entities_referenced": {"gmail": [{"id": "msg1", "title": "The Proposal", "snippet": "about the proposal"}]},
    }]
    result = await compute.resolve_reference({
        "query": "That email about the proposal, who sent it?", "conversation_history": history,
    })
    assert result["resolved"] is True
    assert result["item_id"] == "msg1"


async def test_resolve_reference_most_recent_turn_wins():
    history = [
        {"query": "old", "entities_referenced": {"gmail": [{"id": "old_msg", "title": "budget report", "snippet": ""}]}},
        {"query": "new", "entities_referenced": {"gmail": [{"id": "new_msg", "title": "budget review", "snippet": ""}]}},
    ]
    result = await compute.resolve_reference({"query": "that email about the budget", "conversation_history": history})
    assert result["item_id"] == "new_msg"
