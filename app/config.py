"""Centralized application settings, loaded from environment / .env."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_env: str = "development"
    log_level: str = "INFO"
    secret_key: str = "insecure-dev-key-change-me"

    # Database
    database_url: str = "postgresql+asyncpg://orchestrator:orchestrator@localhost:5432/orchestrator"
    database_url_sync: str = "postgresql+psycopg2://orchestrator:orchestrator@localhost:5432/orchestrator"

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # LLM
    llm_provider: str = "mock"  # anthropic | mock
    embedding_provider: str = "mock"  # openai | mock
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"
    openai_api_key: str | None = None
    openai_embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    # Google
    mock_google_api: bool = True
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str = "http://localhost:8000/api/v1/auth/google/callback"
    google_scopes: str = (
        "https://www.googleapis.com/auth/gmail.modify,"
        "https://www.googleapis.com/auth/calendar,"
        "https://www.googleapis.com/auth/drive"
    )

    # Rate limiting
    rate_limit_per_user_per_hour: int = 100
    google_api_units_per_second: int = 250

    # Sync
    sync_interval_minutes: int = 15

    # Conversation context
    conversation_history_size: int = 5

    @property
    def google_scopes_list(self) -> list[str]:
        return [s.strip() for s in self.google_scopes.split(",") if s.strip()]

    @property
    def effective_llm_provider(self) -> str:
        if self.llm_provider == "anthropic" and self.anthropic_api_key:
            return "anthropic"
        return "mock"

    @property
    def effective_embedding_provider(self) -> str:
        if self.embedding_provider == "openai" and self.openai_api_key:
            return "openai"
        return "mock"


@lru_cache
def get_settings() -> Settings:
    return Settings()
