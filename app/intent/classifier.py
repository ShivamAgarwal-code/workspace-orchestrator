"""LLM-backed intent classifier with conversation-context-aware reference resolution."""
import hashlib

import structlog

from app.cache import query_cache
from app.intent.prompts import CLASSIFIER_SYSTEM_PROMPT, build_classifier_user_message
from app.intent.schemas import INTENT_SCHEMA, Intent
from app.llm.base import ChatMessage, LLMProvider

logger = structlog.get_logger(__name__)


class IntentClassifier:
    def __init__(self, llm: LLMProvider, enable_cache: bool = True):
        self._llm = llm
        self._enable_cache = enable_cache

    async def classify(
        self,
        query: str,
        conversation_history: list[dict] | None = None,
        now_iso: str = "",
        timezone: str = "UTC",
    ) -> Intent:
        history_signature = _history_signature(conversation_history)

        if self._enable_cache:
            try:
                cached = await query_cache.get_cached_intent(query, history_signature)
            except Exception as exc:  # noqa: BLE001 - cache is an optimization, never a hard dependency
                logger.warning("intent_cache_read_failed", error=str(exc))
                cached = None
            if cached is not None:
                return Intent(**cached, raw_query=query)

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

        if self._enable_cache:
            try:
                await query_cache.set_cached_intent(query, history_signature, raw)
            except Exception as exc:  # noqa: BLE001
                logger.warning("intent_cache_write_failed", error=str(exc))

        return Intent(**raw, raw_query=query)


def _history_signature(conversation_history: list[dict] | None) -> str:
    if not conversation_history:
        return "none"
    joined = "|".join(h.get("query", "") for h in conversation_history)
    return hashlib.sha256(joined.encode()).hexdigest()[:16]
