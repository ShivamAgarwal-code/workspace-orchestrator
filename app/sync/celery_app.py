from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "workspace_orchestrator",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.sync.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "sync-all-users": {
            "task": "app.sync.tasks.sync_all_users",
            "schedule": settings.sync_interval_minutes * 60,
        },
    },
)
