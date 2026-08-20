"""Wraps any EmbeddingProvider with a Redis cache, so repeated syncs/searches over unchanged text
(very common — the same email gets searched against many times) skip the external embedding
call entirely."""
from app.cache import query_cache
from app.llm.base import EmbeddingProvider


class CachedEmbeddingProvider(EmbeddingProvider):
    def __init__(self, inner: EmbeddingProvider, model_id: str):
        self._inner = inner
        self._model_id = model_id
        self.dimension = inner.dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        results: list[list[float] | None] = [None] * len(texts)
        missing_indices = []
        for i, text in enumerate(texts):
            cached = await query_cache.get_cached_embedding(self._model_id, text)
            if cached is not None:
                results[i] = cached
            else:
                missing_indices.append(i)

        if missing_indices:
            fresh = await self._inner.embed([texts[i] for i in missing_indices])
            for idx, vector in zip(missing_indices, fresh, strict=True):
                results[idx] = vector
                await query_cache.set_cached_embedding(self._model_id, texts[idx], vector)

        return results
