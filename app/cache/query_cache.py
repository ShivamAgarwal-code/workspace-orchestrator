"""Redis caches for embeddings and intent classifications — the two most expensive/most-repeated
external calls per query. Embeddings are cached longer (identical text -> identical vector
forever, 1hr TTL is just a memory-hygiene bound) than intent classifications (conversation
context can change what the same raw query should mean, so a short 5-minute TTL limits staleness)."""
import hashlib
import json

from app.cache.redis_client import get_redis

EMBEDDING_TTL_SECONDS = 3600
INTENT_TTL_SECONDS = 300


def _key(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("||".join(parts).encode()).hexdigest()
    return f"{prefix}:{digest}"


async def get_cached_embedding(model_id: str, text: str) -> list[float] | None:
    raw = await get_redis().get(_key("emb", model_id, text))
    return json.loads(raw) if raw else None


async def set_cached_embedding(model_id: str, text: str, vector: list[float]) -> None:
    await get_redis().set(_key("emb", model_id, text), json.dumps(vector), ex=EMBEDDING_TTL_SECONDS)


async def get_cached_intent(query: str, history_signature: str) -> dict | None:
    raw = await get_redis().get(_key("intent", query, history_signature))
    return json.loads(raw) if raw else None


async def set_cached_intent(query: str, history_signature: str, intent: dict) -> None:
    await get_redis().set(_key("intent", query, history_signature), json.dumps(intent), ex=INTENT_TTL_SECONDS)
