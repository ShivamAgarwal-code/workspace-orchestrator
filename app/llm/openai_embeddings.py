"""Real OpenAI-backed EmbeddingProvider."""
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.llm.base import EmbeddingProvider

_BATCH_SIZE = 96  # stay comfortably under OpenAI's per-request input limits


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(self, api_key: str, model: str, dimension: int):
        import openai

        self._client = openai.AsyncOpenAI(api_key=api_key)
        self._model = model
        self.dimension = dimension
        self._retryable = (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError)

    def _retry(self):
        return retry(
            retry=retry_if_exception_type(self._retryable),
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
            reraise=True,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        @self._retry()
        async def _call(batch: list[str]) -> list[list[float]]:
            resp = await self._client.embeddings.create(model=self._model, input=batch, dimensions=self.dimension)
            return [d.embedding for d in resp.data]

        results: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_SIZE):
            results.extend(await _call(texts[i : i + _BATCH_SIZE]))
        return results
