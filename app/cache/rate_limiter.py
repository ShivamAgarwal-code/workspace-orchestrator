"""Per-user rate limit (default 100 queries/hour) via a Redis fixed-hour-window counter.
Shared across every API process, so the limit holds under horizontal scaling."""
from datetime import UTC, datetime

from app.cache.redis_client import get_redis
from app.config import get_settings


class RateLimitExceeded(Exception):
    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"rate limit exceeded; retry after {retry_after_seconds}s")


async def check_and_increment(user_id: str) -> None:
    settings = get_settings()
    r = get_redis()
    window = datetime.now(UTC).strftime("%Y%m%d%H")
    key = f"ratelimit:{user_id}:{window}"
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, 3600)
    if count > settings.rate_limit_per_user_per_hour:
        ttl = await r.ttl(key)
        raise RateLimitExceeded(retry_after_seconds=max(ttl, 1))
