from datetime import datetime

from pydantic import BaseModel


class SyncTriggerResponse(BaseModel):
    triggered: bool
    mode: str  # "async" (Celery task queued) | "sync" (ran inline, ?wait=true)
    summary: dict[str, int] | None = None
    task_id: str | None = None


class ServiceSyncStatus(BaseModel):
    service: str
    state: str
    last_synced_at: datetime | None
    items_synced: int
    last_error: str | None


class SyncStatusResponse(BaseModel):
    services: list[ServiceSyncStatus]
