"""Background sync: pulls Gmail/Calendar/Drive into the pgvector cache tables every
`SYNC_INTERVAL_MINUTES` (default 15). Skips re-embedding items whose content hasn't changed
(tracked via `content_hash`), since embedding calls are the expensive part of a sync pass. Each
service is synced and status-tracked independently so one failing service (e.g. a revoked Google
token) never blocks the other two.
"""
import asyncio
import hashlib
import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.client_factory import get_client_bundle
from app.db.base import session_scope
from app.db.models import GCalCache, GDriveCache, GmailCache, SyncService, SyncState, SyncStatus, User
from app.llm.base import EmbeddingProvider
from app.llm.factory import get_embedding_provider
from app.search.chunking import build_embedding_text
from app.sync.celery_app import celery_app

logger = structlog.get_logger(__name__)

LIST_PAGE_SIZE = 200


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@celery_app.task(name="app.sync.tasks.sync_all_users")
def sync_all_users() -> dict:
    return asyncio.run(_sync_all_users())


@celery_app.task(name="app.sync.tasks.sync_user")
def sync_user_task(user_id: str) -> dict:
    return asyncio.run(sync_user_now(uuid.UUID(user_id)))


async def _sync_all_users() -> dict:
    async with session_scope() as session:
        user_ids = list((await session.execute(select(User.id))).scalars().all())
    results = {}
    for user_id in user_ids:
        results[str(user_id)] = await sync_user_now(user_id)
    return results


async def sync_user_now(user_id: uuid.UUID) -> dict:
    """Syncs all three services for one user. Importable directly (used by the Celery task, the
    manual /api/v1/sync/trigger endpoint, and scripts/seed_mock_data.py) so the sync logic lives
    in exactly one place."""
    async with session_scope() as session:
        user = await session.get(User, user_id)
        if user is None:
            return {}
        gmail_client, calendar_client, drive_client = await get_client_bundle(session, user)

    embeddings = get_embedding_provider()
    summary: dict[str, int] = {}

    for service, sync_fn, client in (
        (SyncService.gmail, _sync_gmail, gmail_client),
        (SyncService.gcal, _sync_gcal, calendar_client),
        (SyncService.gdrive, _sync_gdrive, drive_client),
    ):
        try:
            async with session_scope() as session:
                user = await session.get(User, user_id)
                count = await sync_fn(session, user, client, embeddings)
            summary[service.value] = count
            await _update_sync_status(user_id, service, SyncState.idle, count, None)
        except Exception as exc:  # noqa: BLE001 - one service's failure must not sink the others
            logger.warning("sync_service_failed", user_id=str(user_id), service=service.value, error=str(exc))
            await _update_sync_status(user_id, service, SyncState.error, 0, str(exc))

    return summary


async def _update_sync_status(user_id: uuid.UUID, service: SyncService, state: SyncState, items_synced: int, error: str | None) -> None:
    async with session_scope() as session:
        stmt = select(SyncStatus).where(SyncStatus.user_id == user_id, SyncStatus.service == service)
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing is None:
            existing = SyncStatus(user_id=user_id, service=service)
            session.add(existing)
        existing.state = state
        existing.items_synced = items_synced
        existing.last_error = error
        existing.last_synced_at = datetime.now(UTC)


async def _existing_hashes(session: AsyncSession, model, user_id: uuid.UUID, id_col: str) -> dict[str, str]:
    stmt = select(getattr(model, id_col), model.content_hash).where(model.user_id == user_id)
    return {row[0]: row[1] for row in (await session.execute(stmt)).all()}


async def _sync_gmail(session: AsyncSession, user: User, client, embeddings: EmbeddingProvider) -> int:
    items = await client.list_messages(query="", max_results=LIST_PAGE_SIZE)
    existing_hashes = await _existing_hashes(session, GmailCache, user.id, "email_id")

    prepared = [(item, build_embedding_text(item.get("subject"), item.get("body"))) for item in items]
    to_embed = [(i, text) for i, (item, text) in enumerate(prepared) if existing_hashes.get(item["id"]) != _hash(text)]
    vectors = await embeddings.embed([t for _, t in to_embed]) if to_embed else []
    vector_by_index = dict(zip([i for i, _ in to_embed], vectors, strict=True))

    written = 0
    for i, (item, text) in enumerate(prepared):
        content_hash = _hash(text)
        if existing_hashes.get(item["id"]) == content_hash:
            continue
        values = {
            "user_id": user.id,
            "email_id": item["id"],
            "thread_id": item.get("thread_id"),
            "subject": item.get("subject"),
            "body_preview": (item.get("body") or "")[:5000],
            "sender": item.get("from"),
            "recipients": item.get("to") or [],
            "labels": item.get("labels") or [],
            "received_at": item.get("received_at"),
            "content_hash": content_hash,
            "embedding": vector_by_index[i],
            "updated_at": datetime.now(UTC),
        }
        stmt = pg_insert(GmailCache).values(id=uuid.uuid4(), **values).on_conflict_do_update(
            index_elements=["user_id", "email_id"], set_=values,
        )
        await session.execute(stmt)
        written += 1
    await session.commit()
    return written


async def _sync_gcal(session: AsyncSession, user: User, client, embeddings: EmbeddingProvider) -> int:
    items = await client.list_events(max_results=LIST_PAGE_SIZE)
    existing_hashes = await _existing_hashes(session, GCalCache, user.id, "event_id")

    prepared = [(item, build_embedding_text(item.get("title"), item.get("description"))) for item in items]
    to_embed = [(i, text) for i, (item, text) in enumerate(prepared) if existing_hashes.get(item["id"]) != _hash(text)]
    vectors = await embeddings.embed([t for _, t in to_embed]) if to_embed else []
    vector_by_index = dict(zip([i for i, _ in to_embed], vectors, strict=True))

    written = 0
    for i, (item, text) in enumerate(prepared):
        content_hash = _hash(text)
        if existing_hashes.get(item["id"]) == content_hash:
            continue
        values = {
            "user_id": user.id,
            "event_id": item["id"],
            "calendar_id": item.get("calendar_id", "primary"),
            "title": item.get("title"),
            "description": item.get("description"),
            "location": item.get("location"),
            "organizer": item.get("organizer"),
            "attendees": item.get("attendees") or [],
            "status": item.get("status"),
            "start_time": item.get("start_time"),
            "end_time": item.get("end_time"),
            "content_hash": content_hash,
            "embedding": vector_by_index[i],
            "updated_at": datetime.now(UTC),
        }
        stmt = pg_insert(GCalCache).values(id=uuid.uuid4(), **values).on_conflict_do_update(
            index_elements=["user_id", "event_id"], set_=values,
        )
        await session.execute(stmt)
        written += 1
    await session.commit()
    return written


async def _sync_gdrive(session: AsyncSession, user: User, client, embeddings: EmbeddingProvider) -> int:
    items = await client.list_files(max_results=LIST_PAGE_SIZE)
    existing_hashes = await _existing_hashes(session, GDriveCache, user.id, "file_id")

    prepared = [(item, build_embedding_text(item.get("name"), item.get("content_preview"))) for item in items]
    to_embed = [(i, text) for i, (item, text) in enumerate(prepared) if existing_hashes.get(item["id"]) != _hash(text)]
    vectors = await embeddings.embed([t for _, t in to_embed]) if to_embed else []
    vector_by_index = dict(zip([i for i, _ in to_embed], vectors, strict=True))

    written = 0
    for i, (item, text) in enumerate(prepared):
        content_hash = _hash(text)
        if existing_hashes.get(item["id"]) == content_hash:
            continue
        extra_metadata = None
        if item.get("ooo_start") and item.get("ooo_end"):
            extra_metadata = {"ooo_start": item["ooo_start"].isoformat(), "ooo_end": item["ooo_end"].isoformat()}
        values = {
            "user_id": user.id,
            "file_id": item["id"],
            "name": item.get("name"),
            "mime_type": item.get("mime_type"),
            "content_preview": item.get("content_preview"),
            "owners": item.get("owners") or [],
            "web_view_link": item.get("web_view_link"),
            "parent_folder_id": item.get("parent_folder_id"),
            "modified_at": item.get("modified_at"),
            "content_hash": content_hash,
            "extra_metadata": extra_metadata,
            "embedding": vector_by_index[i],
            "updated_at": datetime.now(UTC),
        }
        stmt = pg_insert(GDriveCache).values(id=uuid.uuid4(), **values).on_conflict_do_update(
            index_elements=["user_id", "file_id"], set_=values,
        )
        await session.execute(stmt)
        written += 1
    await session.commit()
    return written
