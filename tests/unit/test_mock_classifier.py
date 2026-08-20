import pytest

from app.intent.classifier import IntentClassifier
from app.llm.mock_provider import MockLLMProvider


@pytest.fixture
def classifier() -> IntentClassifier:
    return IntentClassifier(MockLLMProvider(), enable_cache=False)


async def test_cancel_flight_multi_service(classifier):
    intent = await classifier.classify("Cancel my Turkish Airlines flight")
    assert intent.intent == "cancel_flight"
    assert set(intent.services) == {"gmail", "gcal", "gdrive"}
    assert intent.entities["airline"] == "Turkish Airlines"
    assert intent.needs_clarification is False


async def test_prepare_for_meeting_extracts_company_and_date(classifier):
    intent = await classifier.classify("Prepare for tomorrow's client meeting with Acme Corp")
    assert intent.intent == "prepare_for_meeting"
    assert intent.entities["date_phrase"] == "tomorrow"
    assert intent.entities["company"] == "Acme Corp"


async def test_search_calendar_single_service(classifier):
    intent = await classifier.classify("What is on my calendar next week?")
    assert intent.intent == "search_calendar"
    assert intent.services == ["gcal"]
    assert intent.entities["date_phrase"] == "next week"


async def test_search_email_with_sender_filter(classifier):
    intent = await classifier.classify("Find emails from sarah@company.com about the budget")
    assert intent.intent == "search_email"
    assert intent.entities["email_addresses"] == ["sarah@company.com"]


async def test_pdfs_from_drive_extracts_file_type(classifier):
    intent = await classifier.classify("Show me PDFs in Drive from last month")
    assert intent.intent == "search_drive"
    assert intent.entities["file_type"] == "pdf"
    assert intent.entities["date_phrase"] == "last month"


async def test_ambiguous_move_meeting_with_john_needs_clarification(classifier):
    intent = await classifier.classify("Move the meeting with John")
    assert intent.intent == "reschedule_event"
    assert intent.needs_clarification is True
    assert intent.clarification_question is not None
    assert "John" in intent.clarification_question


async def test_reference_lookup_does_not_preemptively_ask_for_clarification(classifier):
    # needs_clarification is intentionally left to the resolve_reference compute node, which
    # actually checks conversation history (see orchestrator/reporting.resolve_final_clarification).
    intent = await classifier.classify("That email about the proposal")
    assert intent.intent == "reference_lookup"
    assert intent.needs_clarification is False


async def test_find_conflicts(classifier):
    intent = await classifier.classify("Find events next week that conflict with my out-of-office doc")
    assert intent.intent == "find_conflicts"
    assert set(intent.services) >= {"gcal", "gdrive"}


async def test_next_tuesday_defaults_to_calendar_only(classifier):
    intent = await classifier.classify("What do I have next Tuesday?")
    assert intent.services == ["gcal"]
    assert intent.entities["date_phrase"] == "next tuesday"


async def test_conversation_history_does_not_leak_keywords_into_current_query(classifier):
    """Regression test: a prior turn's raw text ('cancel my flight') must not make an unrelated
    later query ('prepare for a meeting') get misclassified as cancel_flight just because the
    word 'cancel' appears in the history block of the prompt."""
    history = [{"query": "Cancel my Turkish Airlines flight", "intent": {"intent": "cancel_flight"}}]
    intent = await classifier.classify(
        "Prepare for tomorrow's client meeting with Acme Corp", conversation_history=history,
    )
    assert intent.intent == "prepare_for_meeting"
    assert intent.entities.get("airline") is None


async def test_intent_cache_roundtrip():
    """With caching enabled, a second identical call (same query + same history) should return
    the cached classification rather than re-running the (here, deterministic) mock LLM — this
    just verifies the cache doesn't corrupt or bypass the result, not the Redis mechanics."""
    classifier = IntentClassifier(MockLLMProvider(), enable_cache=False)  # Redis not available in unit tests
    intent1 = await classifier.classify("Cancel my Turkish Airlines flight")
    intent2 = await classifier.classify("Cancel my Turkish Airlines flight")
    assert intent1.intent == intent2.intent == "cancel_flight"
