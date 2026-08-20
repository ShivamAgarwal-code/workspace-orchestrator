from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.db.models import SyncStatus, User
from app.dependencies import get_current_user
from app.schemas.sync import ServiceSyncStatus, SyncStatusResponse, SyncTriggerResponse

router = APIRouter(prefix="/api/v1/sync", tags=["sync"])


@router.post("/trigger", response_model=SyncTriggerResponse)
async def trigger_sync(wait: bool = False, user: User = Depends(get_current_user)) -> SyncTriggerResponse:
    if wait:
        from app.sync.tasks import sync_user_now

        summary = await sync_user_now(user.id)
        return SyncTriggerResponse(triggered=True, mode="sync", summary=summary)

    from app.sync.tasks import sync_user_task

    async_result = sync_user_task.delay(str(user.id))
    return SyncTriggerResponse(triggered=True, mode="async", task_id=async_result.id)


@router.get("/status", response_model=SyncStatusResponse)
async def sync_status(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> SyncStatusResponse:
    rows = (await session.execute(select(SyncStatus).where(SyncStatus.user_id == user.id))).scalars().all()
    return SyncStatusResponse(
        services=[
            ServiceSyncStatus(
                service=row.service.value,
                state=row.state.value,
                last_synced_at=row.last_synced_at,
                items_synced=row.items_synced,
                last_error=row.last_error,
            )
            for row in rows
        ]
    )
