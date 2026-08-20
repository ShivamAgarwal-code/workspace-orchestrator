"""SQLAlchemy models mirroring the schema in DESIGN.md.

Three near-identical *_cache tables (gmail/gcal/gdrive) are kept separate rather than a single
polymorphic table: each has different filterable metadata (sender vs attendees vs mime_type) that
benefit from dedicated btree/GIN indexes, and separate tables let each service's sync job write
independently without lock contention on a shared table.
"""
import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import get_settings
from app.db.base import Base

EMBEDDING_DIM = get_settings().embedding_dim


class SyncService(str, enum.Enum):
    gmail = "gmail"
    gcal = "gcal"
    gdrive = "gdrive"


class SyncState(str, enum.Enum):
    idle = "idle"
    running = "running"
    error = "error"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    google_access_token: Mapped[str | None] = mapped_column(Text)
    google_refresh_token: Mapped[str | None] = mapped_column(Text)
    google_token_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    query: Mapped[str] = mapped_column(Text)
    intent: Mapped[dict | None] = mapped_column(JSONB)
    response: Mapped[str | None] = mapped_column(Text)
    actions_taken: Mapped[list | None] = mapped_column(JSONB)
    entities_referenced: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    user: Mapped["User"] = relationship(back_populates="conversations")

    __table_args__ = (Index("ix_conversations_user_created", "user_id", "created_at"),)


class GmailCache(Base):
    __tablename__ = "gmail_cache"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    email_id: Mapped[str] = mapped_column(String(255))
    thread_id: Mapped[str | None] = mapped_column(String(255), index=True)
    subject: Mapped[str | None] = mapped_column(Text)
    body_preview: Mapped[str | None] = mapped_column(Text)
    sender: Mapped[str | None] = mapped_column(String(320), index=True)
    recipients: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    labels: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "email_id", name="uq_gmail_cache_user_email"),
        Index("ix_gmail_cache_user_received", "user_id", "received_at"),
        Index(
            "ix_gmail_cache_embedding",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class GCalCache(Base):
    __tablename__ = "gcal_cache"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[str] = mapped_column(String(255))
    calendar_id: Mapped[str] = mapped_column(String(255), default="primary")
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    organizer: Mapped[str | None] = mapped_column(String(320))
    attendees: Mapped[list[str] | None] = mapped_column(ARRAY(String), index=True)
    status: Mapped[str | None] = mapped_column(String(32))
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "event_id", name="uq_gcal_cache_user_event"),
        Index("ix_gcal_cache_user_start", "user_id", "start_time"),
        Index("ix_gcal_cache_attendees", "attendees", postgresql_using="gin"),
        Index(
            "ix_gcal_cache_embedding",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class GDriveCache(Base):
    __tablename__ = "gdrive_cache"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    file_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(String(128), index=True)
    content_preview: Mapped[str | None] = mapped_column(Text)
    owners: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    web_view_link: Mapped[str | None] = mapped_column(Text)
    parent_folder_id: Mapped[str | None] = mapped_column(String(255))
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    # Structured hints extracted from file content at ingestion time (e.g. an out-of-office doc's
    # date range) so downstream compute nodes (conflict detection) don't need to re-parse free text.
    extra_metadata: Mapped[dict | None] = mapped_column(JSONB)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "file_id", name="uq_gdrive_cache_user_file"),
        Index("ix_gdrive_cache_user_modified", "user_id", "modified_at"),
        Index(
            "ix_gdrive_cache_embedding",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class SyncStatus(Base):
    __tablename__ = "sync_status"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    service: Mapped[SyncService] = mapped_column(Enum(SyncService, name="sync_service"))
    state: Mapped[SyncState] = mapped_column(Enum(SyncState, name="sync_state"), default=SyncState.idle)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    items_synced: Mapped[int] = mapped_column(default=0)
    sync_cursor: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("user_id", "service", name="uq_sync_status_user_service"),)


class AuditLog(Base):
    """Records write operations (send/create/delete) for security/compliance review."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"))
    service: Mapped[str] = mapped_column(String(32))
    operation: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    result: Mapped[str] = mapped_column(String(16))  # success | error
    error_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
