"""Converts a classified Intent into an ExecutionDAG.

Each known intent has a hand-written template that encodes the right mix of parallel and
sequential operations (e.g. "cancel my flight" searches Gmail and Calendar in parallel, then
drafts an email that depends on both results). Unknown/low-confidence intents fall back to a
generic fan-out: parallel search across every service the classifier flagged.
"""
from app.intent.schemas import Intent
from app.orchestrator.types import NodeResult
from app.planner.dag import ExecutionDAG, PlanNode


def _first(result: NodeResult | None):
    """Best search hit from a completed search node, or None if missing/empty/errored."""
    if result is None or result.status != "ok" or not result.data:
        return None
    return result.data[0]


def _entity_text(intent: Intent, *keys: str, default: str = "") -> str:
    for key in keys:
        val = intent.entities.get(key)
        if val:
            return val if isinstance(val, str) else " ".join(val)
    return default or intent.raw_query


class QueryPlanner:
    def build_plan(self, intent: Intent) -> ExecutionDAG:
        # _TEMPLATES holds plain (unbound) function objects, so both the lookup and the
        # default must be called the same way: builder(self, intent).
        builder = self._TEMPLATES.get(intent.intent, QueryPlanner._build_generic_fanout)
        return builder(self, intent)

    # ---- known-intent templates -------------------------------------------------------------

    def _build_cancel_flight(self, intent: Intent) -> ExecutionDAG:
        airline = _entity_text(intent, "airline", default="flight booking")
        dag = ExecutionDAG()
        dag.add_node(PlanNode(
            id="find_booking_email", agent="gmail", operation="search",
            build_params=lambda i, r: {"query": f"{airline} booking confirmation reservation", "limit": 5},
            description="Search Gmail for the flight booking/confirmation email.",
        ))
        dag.add_node(PlanNode(
            id="find_calendar_event", agent="gcal", operation="search",
            build_params=lambda i, r: {"query": f"{airline} flight", "limit": 5},
            description="Search Calendar for the flight event.",
        ))

        def _draft_params(i: Intent, r: dict[str, NodeResult]) -> dict:
            email_hit = _first(r.get("find_booking_email"))
            event_hit = _first(r.get("find_calendar_event"))
            booking_ref = (email_hit.metadata.get("booking_reference") if email_hit else None) or "your booking"
            support_addr = (email_hit.metadata.get("sender") if email_hit else None) or f"support@{airline.lower().replace(' ', '')}.com"
            flight_desc = event_hit.title if event_hit else f"{airline} flight"
            return {
                "to": support_addr,
                "subject": f"Cancellation request - {booking_ref}",
                "body": (
                    f"Hello,\n\nPlease cancel my booking {booking_ref} for {flight_desc}. "
                    "Kindly confirm the cancellation and any applicable refund.\n\nThank you."
                ),
            }

        dag.add_node(PlanNode(
            id="draft_cancellation_email", agent="gmail", operation="execute", action="draft_email",
            build_params=_draft_params, depends_on=["find_booking_email", "find_calendar_event"],
            is_write=True, description="Draft (not send) the cancellation email for user review.",
        ))
        return dag

    def _build_prepare_for_meeting(self, intent: Intent) -> ExecutionDAG:
        who = _entity_text(intent, "company", "person", default="tomorrow's meeting")
        date_phrase = intent.entities.get("date_phrase", "tomorrow")
        dag = ExecutionDAG()
        dag.add_node(PlanNode(
            id="find_meeting_event", agent="gcal", operation="search",
            build_params=lambda i, r: {"query": who, "filters": {"date_phrase": date_phrase}, "limit": 3},
            description="Find the calendar event for the meeting.",
        ))
        dag.add_node(PlanNode(
            id="find_drive_docs", agent="gdrive", operation="search",
            build_params=lambda i, r: {"query": who, "limit": 5},
            description="Find related Drive documents (runs in parallel with the calendar lookup).",
        ))

        def _email_search_params(i: Intent, r: dict[str, NodeResult]) -> dict:
            event = _first(r.get("find_meeting_event"))
            attendees = (event.metadata.get("attendees") if event else None) or []
            query = " ".join(attendees) if attendees else who
            return {"query": query, "limit": 5}

        dag.add_node(PlanNode(
            id="find_participant_emails", agent="gmail", operation="search",
            build_params=_email_search_params, depends_on=["find_meeting_event"],
            description="Search emails with the meeting attendees (depends on knowing who they are).",
        ))
        return dag

    def _build_reschedule_event(self, intent: Intent) -> ExecutionDAG:
        who = _entity_text(intent, "person", "company", default=intent.raw_query)
        dag = ExecutionDAG()
        dag.add_node(PlanNode(
            id="find_candidate_events", agent="gcal", operation="search",
            build_params=lambda i, r: {"query": who, "limit": 10},
            description="Find candidate meetings matching the referenced person/topic.",
        ))
        dag.add_node(PlanNode(
            id="update_event", agent="gcal", operation="execute", action="update_event",
            build_params=lambda i, r: {"event_ref": _first(r.get("find_candidate_events"))},
            depends_on=["find_candidate_events"], is_write=True,
            description="Reschedule the event (blocked if the classifier flagged ambiguity).",
        ))
        return dag

    def _build_find_conflicts(self, intent: Intent) -> ExecutionDAG:
        date_phrase = intent.entities.get("date_phrase", "next week")
        dag = ExecutionDAG()
        dag.add_node(PlanNode(
            id="search_events", agent="gcal", operation="search",
            build_params=lambda i, r: {"query": "*", "filters": {"date_phrase": date_phrase}, "limit": 50},
            description="List calendar events in the target window.",
        ))
        dag.add_node(PlanNode(
            id="search_oof_doc", agent="gdrive", operation="search",
            build_params=lambda i, r: {"query": "out of office schedule", "limit": 3},
            description="Find the out-of-office document (runs in parallel with the calendar listing).",
        ))
        dag.add_node(PlanNode(
            id="detect_conflicts", agent="compute", operation="compute",
            build_params=lambda i, r: {"events": r.get("search_events"), "doc": r.get("search_oof_doc")},
            depends_on=["search_events", "search_oof_doc"],
            description="Cross-reference calendar events against the out-of-office doc's dates.",
        ))
        return dag

    def _build_reference_lookup(self, intent: Intent) -> ExecutionDAG:
        dag = ExecutionDAG()
        dag.add_node(PlanNode(
            id="resolve_reference", agent="compute", operation="compute",
            build_params=lambda i, r: {"query": i.raw_query, "entities": i.entities},
            description="Resolve a pronoun/reference ('that email') against recent conversation history.",
        ))
        dag.add_node(PlanNode(
            id="get_referenced_context", agent="gmail", operation="get_context",
            build_params=lambda i, r: {"item_id": (r.get("resolve_reference").data or {}).get("item_id")
                                        if r.get("resolve_reference") else None},
            depends_on=["resolve_reference"], optional=True,
            description="Fetch full content of the resolved item.",
        ))
        return dag

    def _build_single_service_search(self, intent: Intent, service: str) -> ExecutionDAG:
        dag = ExecutionDAG()
        dag.add_node(PlanNode(
            id=f"search_{service}", agent=service, operation="search",
            build_params=lambda i, r: {"query": i.raw_query, "filters": i.entities, "limit": 10},
            description=f"Search {service}.",
        ))
        return dag

    def _build_search_calendar(self, intent: Intent) -> ExecutionDAG:
        return self._build_single_service_search(intent, "gcal")

    def _build_search_email(self, intent: Intent) -> ExecutionDAG:
        return self._build_single_service_search(intent, "gmail")

    def _build_search_drive(self, intent: Intent) -> ExecutionDAG:
        return self._build_single_service_search(intent, "gdrive")

    def _build_generic_fanout(self, intent: Intent) -> ExecutionDAG:
        dag = ExecutionDAG()
        services = intent.services or ["gmail", "gcal", "gdrive"]
        for service in services:
            dag.add_node(PlanNode(
                id=f"search_{service}", agent=service, operation="search",
                build_params=lambda i, r: {"query": i.raw_query, "limit": 10},
                description=f"Fallback search of {service} for a query without a specific template.",
            ))
        return dag

    _TEMPLATES = {
        "cancel_flight": _build_cancel_flight,
        "prepare_for_meeting": _build_prepare_for_meeting,
        "reschedule_event": _build_reschedule_event,
        "find_conflicts": _build_find_conflicts,
        "reference_lookup": _build_reference_lookup,
        "search_calendar": _build_search_calendar,
        "search_email": _build_search_email,
        "search_drive": _build_search_drive,
    }
