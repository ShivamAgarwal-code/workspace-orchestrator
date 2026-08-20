"""Provider-agnostic interfaces for chat/structured-output LLM calls and embeddings.

Kept deliberately thin (no agent framework) — the orchestrator drives all control flow itself;
these classes are pure I/O adapters around a chat-completion API and an embedding API.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ChatMessage:
    role: str  # "user" | "assistant"
    content: str


class LLMProvider(ABC):
    """Chat-completion + forced structured-output (JSON) interface."""

    @abstractmethod
    async def complete(self, system: str, messages: list[ChatMessage], max_tokens: int = 1024) -> str:
        """Free-form text completion, used by the response synthesizer."""

    @abstractmethod
    async def structured_complete(
        self,
        system: str,
        messages: list[ChatMessage],
        schema: dict,
        tool_name: str = "emit_result",
        max_tokens: int = 1024,
    ) -> dict:
        """Force the model to return JSON conforming to `schema`, used by the intent classifier."""


class EmbeddingProvider(ABC):
    dimension: int

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts, returning one vector per input in the same order."""

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]
