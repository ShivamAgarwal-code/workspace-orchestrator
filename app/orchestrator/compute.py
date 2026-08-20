"""Local (non-Google-API) computation steps that plug into the DAG as agent="compute" nodes.

Kept separate from the agents so plan nodes that are pure data transforms over already-fetched
results (cross-referencing calendar events against a document's date range, resolving "that
email" against conversation history) don't need a fake service client.
"""
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import datetime

HANDLERS: dict[str, Callable[[dict], Awaitable[dict]]] = {}


def register(name: str):
    def deco(fn):
        HANDLERS[name] = fn
        return fn

    return deco


@register("detect_conflicts")
async def detect_conflicts(params: dict) -> dict:
    events_result = params.get("events")
    doc_result = params.get("doc")
    events = events_result.data if events_result and events_result.status == "ok" else []
    doc_hits = doc_result.data if doc_result and doc_result.status == "ok" else []

    windows: list[tuple[datetime, datetime, str]] = []
    for hit in doc_hits:
        start_raw, end_raw = hit.metadata.get("ooo_start"), hit.metadata.get("ooo_end")
        if start_raw and end_raw:
            windows.append((datetime.fromisoformat(start_raw), datetime.fromisoformat(end_raw), hit.title))

    conflicts = []
    for event in events:
        if not event.timestamp:
            continue
        for start, end, doc_title in windows:
            if start <= event.timestamp <= end:
                conflicts.append({
                    "event": asdict(event) if not isinstance(event, dict) else event,
                    "conflicting_document": doc_title,
                    "window": [start.isoformat(), end.isoformat()],
                })

    return {"conflicts": conflicts, "checked_events": len(events), "windows_checked": len(windows)}


@register("resolve_reference")
async def resolve_reference(params: dict) -> dict:
    """Resolve a vague reference ("that email about the proposal") against recent conversation
    history stored in `params["conversation_history"]` (list of {query, intent, entities_referenced}).
    """
    query = (params.get("query") or "").lower()
    history = params.get("conversation_history") or []

    stopwords = {"that", "this", "email", "about", "what", "who", "sent", "the", "was", "did"}
    words = re.findall(r"[a-z0-9]+", query)
    keywords = [w for w in words if len(w) > 3 and w not in stopwords]

    best_match = None
    for turn in reversed(history):  # most recent first
        referenced = turn.get("entities_referenced") or {}
        candidates = referenced.get("gmail", []) if isinstance(referenced, dict) else []
        for item in candidates:
            haystack = f"{item.get('title', '')} {item.get('snippet', '')}".lower()
            if not keywords or any(k in haystack for k in keywords):
                best_match = item
                break
        if best_match:
            break

    if not best_match:
        return {"resolved": False, "item_id": None}
    return {"resolved": True, "item_id": best_match.get("id"), "service": "gmail"}
