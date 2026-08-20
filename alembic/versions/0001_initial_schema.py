"""initial schema: users, conversations, gmail/gcal/gdrive cache, sync_status, audit_log

Revision ID: 0001
Revises:
Create Date: 2026-08-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

EMBEDDING_DIM = 1536


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    sync_service = postgresql.ENUM("gmail", "gcal", "gdrive", name="sync_service")
    sync_state = postgresql.ENUM("idle", "running", "error", name="sync_state")
    sync_service.create(op.get_bind(), checkfirst=True)
    sync_state.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("google_access_token", sa.Text()),
        sa.Column("google_refresh_token", sa.Text()),
        sa.Column("google_token_expiry", sa.DateTime(timezone=True)),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("intent", postgresql.JSONB()),
        sa.Column("response", sa.Text()),
        sa.Column("actions_taken", postgresql.JSONB()),
        sa.Column("entities_referenced", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])
    op.create_index("ix_conversations_user_created", "conversations", ["user_id", "created_at"])

    op.create_table(
        "gmail_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email_id", sa.String(255), nullable=False),
        sa.Column("thread_id", sa.String(255)),
        sa.Column("subject", sa.Text()),
        sa.Column("body_preview", sa.Text()),
        sa.Column("sender", sa.String(320)),
        sa.Column("recipients", postgresql.ARRAY(sa.String())),
        sa.Column("labels", postgresql.ARRAY(sa.String())),
        sa.Column("received_at", sa.DateTime(timezone=True)),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("embedding", Vector(EMBEDDING_DIM)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "email_id", name="uq_gmail_cache_user_email"),
    )
    op.create_index("ix_gmail_cache_user_id", "gmail_cache", ["user_id"])
    op.create_index("ix_gmail_cache_thread_id", "gmail_cache", ["thread_id"])
    op.create_index("ix_gmail_cache_sender", "gmail_cache", ["sender"])
    op.create_index("ix_gmail_cache_received_at", "gmail_cache", ["received_at"])
    op.create_index("ix_gmail_cache_user_received", "gmail_cache", ["user_id", "received_at"])
    op.execute(
        "CREATE INDEX ix_gmail_cache_embedding ON gmail_cache "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )

    op.create_table(
        "gcal_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", sa.String(255), nullable=False),
        sa.Column("calendar_id", sa.String(255), nullable=False, server_default="primary"),
        sa.Column("title", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("location", sa.Text()),
        sa.Column("organizer", sa.String(320)),
        sa.Column("attendees", postgresql.ARRAY(sa.String())),
        sa.Column("status", sa.String(32)),
        sa.Column("start_time", sa.DateTime(timezone=True)),
        sa.Column("end_time", sa.DateTime(timezone=True)),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("embedding", Vector(EMBEDDING_DIM)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "event_id", name="uq_gcal_cache_user_event"),
    )
    op.create_index("ix_gcal_cache_user_id", "gcal_cache", ["user_id"])
    op.create_index("ix_gcal_cache_start_time", "gcal_cache", ["start_time"])
    op.create_index("ix_gcal_cache_user_start", "gcal_cache", ["user_id", "start_time"])
    op.create_index("ix_gcal_cache_attendees", "gcal_cache", ["attendees"], postgresql_using="gin")
    op.execute(
        "CREATE INDEX ix_gcal_cache_embedding ON gcal_cache "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )

    op.create_table(
        "gdrive_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_id", sa.String(255), nullable=False),
        sa.Column("name", sa.Text()),
        sa.Column("mime_type", sa.String(128)),
        sa.Column("content_preview", sa.Text()),
        sa.Column("owners", postgresql.ARRAY(sa.String())),
        sa.Column("web_view_link", sa.Text()),
        sa.Column("parent_folder_id", sa.String(255)),
        sa.Column("modified_at", sa.DateTime(timezone=True)),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("embedding", Vector(EMBEDDING_DIM)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "file_id", name="uq_gdrive_cache_user_file"),
    )
    op.create_index("ix_gdrive_cache_user_id", "gdrive_cache", ["user_id"])
    op.create_index("ix_gdrive_cache_mime_type", "gdrive_cache", ["mime_type"])
    op.create_index("ix_gdrive_cache_modified_at", "gdrive_cache", ["modified_at"])
    op.create_index("ix_gdrive_cache_user_modified", "gdrive_cache", ["user_id", "modified_at"])
    op.execute(
        "CREATE INDEX ix_gdrive_cache_embedding ON gdrive_cache "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )

    op.create_table(
        "sync_status",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("service", sync_service, nullable=False),
        sa.Column("state", sync_state, nullable=False, server_default="idle"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("items_synced", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sync_cursor", sa.Text()),
        sa.UniqueConstraint("user_id", "service", name="uq_sync_status_user_service"),
    )
    op.create_index("ix_sync_status_user_id", "sync_status", ["user_id"])

    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="SET NULL")),
        sa.Column("service", sa.String(32), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB()),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("error_detail", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("sync_status")
    op.drop_table("gdrive_cache")
    op.drop_table("gcal_cache")
    op.drop_table("gmail_cache")
    op.drop_table("conversations")
    op.drop_table("users")
    postgresql.ENUM(name="sync_state").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="sync_service").drop(op.get_bind(), checkfirst=True)
