"""Shared agent interface. Every service agent (Gmail/GCal/Drive) normalizes its results to
`SearchResult` so the planner, orchestrator, and synthesizer can treat all three uniformly.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class SearchResult:
    id: str
    service: str
    title: str
    snippet: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "service": self.service,
            "title": self.title,
            "snippet": self.snippet,
            "score": self.score,
            "metadata": self.metadata,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


class AgentError(Exception):
    """Raised by an agent operation; carries whether the caller should retry."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class BaseAgent(ABC):
    service_name: str

    @abstractmethod
    async def search(self, query: str, filters: dict | None = None, limit: int = 10) -> list[SearchResult]:
        """Hybrid (vector + metadata) search over this service's cached, embedded items."""

    @abstractmethod
    async def execute(self, action: str, params: dict) -> dict:
        """Perform a write operation (send/create/update/delete/draft/share/move/...)."""

    @abstractmethod
    async def get_context(self, item_id: str) -> dict:
        """Fetch full content for one item, for LLM reasoning (e.g. full email body)."""
