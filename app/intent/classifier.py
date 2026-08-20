"""LLM-backed intent classifier with conversation-context-aware reference resolution."""
from app.intent.prompts import CLASSIFIER_SYSTEM_PROMPT, build_classifier_user_message
from app.intent.schemas import INTENT_SCHEMA, Intent
from app.llm.base import ChatMessage, LLMProvider


class IntentClassifier:
    def __init__(self, llm: LLMProvider):
        self._llm = llm

    async def classify(
        self,
        query: str,
        conversation_history: list[dict] | None = None,
        now_iso: str = "",
        timezone: str = "UTC",
    ) -> Intent:
        user_message = build_classifier_user_message(query, conversation_history, now_iso, timezone)
        raw = await self._llm.structured_complete(
            system=CLASSIFIER_SYSTEM_PROMPT,
            messages=[ChatMessage(role="user", content=user_message)],
            schema=INTENT_SCHEMA,
            tool_name="classify_intent",
        )
        raw.setdefault("services", [])
        raw.setdefault("entities", {})
        raw.setdefault("steps", [])
        raw.setdefault("confidence", 0.5)
        raw.setdefault("needs_clarification", False)
        raw.setdefault("clarification_question", None)
        raw.setdefault("intent", "general_query")
        return Intent(**raw, raw_query=query)
