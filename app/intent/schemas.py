"""Structured representation of a classified user query."""
from enum import StrEnum

from pydantic import BaseModel, Field

# JSON Schema handed to the LLM for forced structured output (Anthropic tool-use / function calling).
INTENT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "services": {
            "type": "array",
            "items": {"type": "string", "enum": ["gmail", "gcal", "gdrive"]},
            "description": "Google Workspace services this query needs.",
        },
        "intent": {
            "type": "string",
            "description": "Short snake_case intent label, e.g. cancel_flight, prepare_for_meeting, "
            "search_calendar, search_email, search_drive, reschedule_event, find_conflicts, "
            "reference_lookup, general_query.",
        },
        "entities": {
            "type": "object",
            "description": "Extracted entities: airline, person, company, date_phrase, email_addresses, "
            "file_type, event_title, etc. Keys are free-form but values must be strings or arrays of strings.",
        },
        "steps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Ordered high-level plan steps, e.g. ['search_gmail_for_booking', "
            "'find_calendar_event', 'draft_cancellation_email'].",
        },
        "confidence": {"type": "number", "description": "0-1 confidence in this classification."},
        "needs_clarification": {
            "type": "boolean",
            "description": "True if the query is ambiguous (unclear referent, missing target) and the "
            "user should be asked a follow-up question before executing any write operation.",
        },
        "clarification_question": {
            "type": ["string", "null"],
            "description": "The follow-up question to ask when needs_clarification is true, else null.",
        },
    },
    "required": ["services", "intent", "entities", "steps", "confidence", "needs_clarification"],
}


class IntentName(StrEnum):
    cancel_flight = "cancel_flight"
    prepare_for_meeting = "prepare_for_meeting"
    search_calendar = "search_calendar"
    search_email = "search_email"
    search_drive = "search_drive"
    reschedule_event = "reschedule_event"
    find_conflicts = "find_conflicts"
    reference_lookup = "reference_lookup"
    general_query = "general_query"


class Intent(BaseModel):
    services: list[str] = Field(default_factory=list)
    intent: str = IntentName.general_query
    entities: dict = Field(default_factory=dict)
    steps: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    needs_clarification: bool = False
    clarification_question: str | None = None
    raw_query: str = ""
