"""Deterministic, key-free stand-ins for LLMProvider/EmbeddingProvider.

These let the entire orchestrator (classification -> planning -> execution -> search ->
synthesis) run end-to-end with zero external API keys, which is what makes the Docker Compose
stack demoable and the test suite fast/offline. `MockLLMProvider` implements the two concrete
capabilities this codebase actually needs from an LLM (intent classification via forced
structured output, and free-text response synthesis) with real rule-based NLU rather than
returning a canned stub, so mock mode still produces sensible, query-dependent output.
"""
import hashlib
import json
import re

from app.llm.base import ChatMessage, EmbeddingProvider, LLMProvider

AIRLINES = [
    "turkish airlines", "delta", "united airlines", "american airlines", "lufthansa",
    "emirates", "qatar airways", "british airways", "air france", "klm", "southwest",
]

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_DATE_PHRASE_RE = re.compile(
    r"\b(today|tomorrow|yesterday|"
    r"next week|last week|this week|"
    r"next month|last month|this month|"
    r"next (?:mon|tues|wednes|thurs|fri|satur|sun)day|"
    r"(?:mon|tues|wednes|thurs|fri|satur|sun)day)\b",
    re.IGNORECASE,
)
_PERSON_AFTER_WITH_RE = re.compile(r"\bwith\s+([A-Z][a-zA-Z]+)(?!\s+[A-Z][a-zA-Z]+\s+Corp)\b")
_COMPANY_RE = re.compile(r"\b([A-Z][a-zA-Z]*\s?(?:Corp|Inc|LLC|Ltd|Co)\.?)\b")


class MockLLMProvider(LLMProvider):
    """Rule-based classification + templated synthesis, keyed by tool_name/prompt shape."""

    async def structured_complete(
        self,
        system: str,
        messages: list[ChatMessage],
        schema: dict,
        tool_name: str = "emit_result",
        max_tokens: int = 1024,
    ) -> dict:
        prompt = messages[-1].content if messages else ""
        if tool_name == "classify_intent":
            # The prompt is "<conversation history>\n\nCurrent time: ...\n\nUser query: <query>" —
            # classify only the current turn, never the history block (a prior turn's "cancel my
            # flight" line must not leak the word "cancel" into an unrelated later query).
            marker = "User query: "
            query = prompt.split(marker, 1)[1] if marker in prompt else prompt
            return self._classify_intent(query)
        # Generic fallback for any other forced-schema call: produce a minimally valid
        # object by filling required fields with type-appropriate zero values.
        return _fill_schema_stub(schema)

    async def complete(self, system: str, messages: list[ChatMessage], max_tokens: int = 1024) -> str:
        prompt = messages[-1].content if messages else ""
        match = re.search(r"```json\s*(\{.*?\})\s*```", prompt, re.DOTALL)
        if not match:
            match = re.search(r"(\{.*\})", prompt, re.DOTALL)
        if match:
            try:
                context = json.loads(match.group(1))
                return _synthesize_from_context(context)
            except (json.JSONDecodeError, TypeError):
                pass
        return "I processed your request but could not generate a detailed summary."

    def _classify_intent(self, query: str) -> dict:
        q = query.lower()
        services: list[str] = []
        entities: dict = {}

        if any(k in q for k in ("email", "inbox", "gmail", "message", "draft", "send")):
            services.append("gmail")
        if any(k in q for k in ("calendar", "meeting", "event", "schedule", "invited", "invite")):
            services.append("gcal")
        if any(k in q for k in ("drive", "document", "pdf", "file", "folder", "doc")):
            services.append("gdrive")

        airline = next((a for a in AIRLINES if a in q), None)
        if airline:
            entities["airline"] = airline.title()
        emails = _EMAIL_RE.findall(query)
        if emails:
            entities["email_addresses"] = emails
        date_match = _DATE_PHRASE_RE.search(q)
        if date_match:
            entities["date_phrase"] = date_match.group(0)
        person_match = _PERSON_AFTER_WITH_RE.search(query)
        company_match = _COMPANY_RE.search(query)
        if company_match:
            entities["company"] = company_match.group(1)
        elif person_match:
            entities["person"] = person_match.group(1)
        for keyword in ("pdf", "doc", "sheet", "spreadsheet", "slide", "presentation", "image", "photo"):
            if keyword in q:
                entities["file_type"] = keyword
                break

        if not services and entities.get("date_phrase") and not entities.get("file_type"):
            # A bare date/time question ("what do I have next Tuesday?") with no other service
            # keyword is, in practice, almost always about the calendar.
            services = ["gcal"]

        intent, steps, needs_clarification, clarification_question = self._route(q, services, entities)

        if not services:
            services = ["gmail", "gcal", "gdrive"]

        confidence = 0.55 if needs_clarification else (0.9 if intent != "general_query" else 0.6)

        return {
            "services": services,
            "intent": intent,
            "entities": entities,
            "steps": steps,
            "confidence": confidence,
            "needs_clarification": needs_clarification,
            "clarification_question": clarification_question,
        }

    def _route(self, q: str, services: list[str], entities: dict) -> tuple[str, list[str], bool, str | None]:
        if "cancel" in q and ("flight" in q or "booking" in q or "reservation" in q or entities.get("airline")):
            return (
                "cancel_flight",
                ["search_gmail_for_booking", "find_calendar_event", "draft_cancellation_email"],
                False,
                None,
            )
        if "prepare" in q and ("meeting" in q or "with" in q):
            return (
                "prepare_for_meeting",
                ["find_calendar_event", "search_gmail_for_participant", "search_drive_for_documents"],
                False,
                None,
            )
        if ("move" in q or "reschedule" in q) and "meeting" in q:
            ambiguous = "person" in entities and "date_phrase" not in entities
            return (
                "reschedule_event",
                ["search_calendar_events_by_attendee", "resolve_ambiguity", "update_event"],
                ambiguous,
                (f"I found more than one meeting that could match \"{entities.get('person')}\". "
                 "Which meeting and new time did you mean?") if ambiguous else None,
            )
        if "conflict" in q or "overlap" in q:
            return (
                "find_conflicts",
                ["search_calendar_events", "search_drive_for_document", "detect_conflicts"],
                False,
                None,
            )
        if "that email" in q or ("email" in q and "about" in q and len(q.split()) < 8 and "find" not in q):
            # needs_clarification is left False here even though "that email" is inherently vague:
            # whether it actually resolves depends on conversation history, which only the
            # resolve_reference compute node (running after this classification) can check. The
            # synthesizer asks the clarifying question itself if that node reports unresolved,
            # rather than the classifier guessing blind.
            return (
                "reference_lookup",
                ["resolve_conversation_reference", "get_email_context"],
                False,
                None,
            )
        if "gcal" in services and "gmail" not in services and "gdrive" not in services:
            return "search_calendar", ["search_calendar_events"], False, None
        if "gmail" in services and "gcal" not in services and "gdrive" not in services:
            return "search_email", ["search_gmail"], False, None
        if "gdrive" in services and "gmail" not in services and "gcal" not in services:
            return "search_drive", ["search_drive"], False, None
        return "general_query", ["search_all_services"], False, None


class MockEmbeddingProvider(EmbeddingProvider):
    """Deterministic hashing-trick embedding (bag-of-words feature hashing, L2-normalized).

    Not semantically as strong as a real embedding model, but it preserves the property that
    matters for testing hybrid search end-to-end without an API key: texts sharing vocabulary
    get high cosine similarity, unrelated texts get low similarity, and results are 100%
    reproducible across runs (needed for the precision@5 eval script and tests).
    """

    def __init__(self, dimension: int = 1536):
        self.dimension = dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dimension
        tokens = re.findall(r"[a-z0-9]+", text.lower())
        for i in range(len(tokens)):
            for n in (1, 2):  # unigrams + bigrams for a little local context
                gram = "_".join(tokens[i : i + n])
                if len(gram.split("_")) != n:
                    continue
                digest = hashlib.sha256(gram.encode()).digest()
                idx = int.from_bytes(digest[:4], "big") % self.dimension
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                vec[idx] += sign
        norm = sum(v * v for v in vec) ** 0.5
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


def _fill_schema_stub(schema: dict) -> dict:
    props = schema.get("properties", {})
    out = {}
    for name, spec in props.items():
        t = spec.get("type")
        if t == "array":
            out[name] = []
        elif t == "object":
            out[name] = {}
        elif t == "number" or t == "integer":
            out[name] = 0
        elif t == "boolean":
            out[name] = False
        else:
            out[name] = ""
    return out


def _synthesize_from_context(context: dict) -> str:
    lines: list[str] = []
    summary = context.get("summary")
    if summary:
        lines.append(str(summary))

    results = context.get("results", {})
    for service, items in results.items():
        if not items:
            continue
        label = {"gmail": "Email", "gcal": "Calendar", "gdrive": "Drive"}.get(service, service)
        lines.append(f"\n{label} results:")
        for item in items[:5]:
            title = item.get("title") or item.get("subject") or item.get("name") or "(untitled)"
            lines.append(f"- {title}")

    referenced = context.get("referenced_item")
    if referenced:
        subject = referenced.get("subject") or referenced.get("title") or "(untitled)"
        sender = referenced.get("from") or referenced.get("sender")
        body = (referenced.get("body") or referenced.get("description") or "").strip()
        lines.append(f"\n\"{subject}\"" + (f" from {sender}" if sender else "") + ":")
        if body:
            lines.append(body[:500])

    conflicts = (context.get("conflicts") or {}).get("conflicts") if isinstance(context.get("conflicts"), dict) else None
    if conflicts:
        lines.append("\nConflicts found:")
        for c in conflicts:
            event_title = (c.get("event") or {}).get("title", "(untitled event)")
            lines.append(f"- \"{event_title}\" overlaps with {c.get('conflicting_document')}")

    errors = context.get("errors", {})
    for service, err in errors.items():
        lines.append(f"\nNote: I could not reach {service} ({err}); the rest of the answer above is still complete.")

    actions = context.get("actions_taken", [])
    for action in actions:
        lines.append(f"\n✓ {action}")

    if context.get("pending_confirmation"):
        lines.append(f"\nWould you like me to {context['pending_confirmation']}?")

    if context.get("needs_clarification") and context.get("clarification_question"):
        lines.append(f"\n{context['clarification_question']}")

    if not lines:
        lines.append("I could not find anything matching your request.")

    return "\n".join(lines).strip()
