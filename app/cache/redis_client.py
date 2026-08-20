from functools import lru_cache

import redis.asyncio as redis

from app.config import get_settings


@lru_cache
def get_redis() -> redis.Redis:
    settings = get_settings()
    return redis.from_url(settings.redis_url, decode_responses=True)


async def close_redis() -> None:
    """Closes the pooled connection and drops the cached client so the next get_redis() call
    reconnects under whatever event loop is current — see app.db.base.dispose_engine for why
    this matters (Celery tasks, test suite: each gets its own event loop)."""
    if get_redis.cache_info().currsize:
        await get_redis().aclose()
    get_redis.cache_clear()
