"""Singleton accessors that pick the real or mock provider based on settings/key availability."""
from functools import lru_cache

from app.config import get_settings
from app.llm.base import EmbeddingProvider, LLMProvider
from app.llm.mock_provider import MockEmbeddingProvider, MockLLMProvider


@lru_cache
def get_llm_provider() -> LLMProvider:
    settings = get_settings()
    if settings.effective_llm_provider == "anthropic":
        from app.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(api_key=settings.anthropic_api_key, model=settings.anthropic_model)
    return MockLLMProvider()


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    settings = get_settings()
    if settings.effective_embedding_provider == "openai":
        from app.llm.openai_embeddings import OpenAIEmbeddingProvider

        return OpenAIEmbeddingProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_embedding_model,
            dimension=settings.embedding_dim,
        )
    return MockEmbeddingProvider(dimension=settings.embedding_dim)
