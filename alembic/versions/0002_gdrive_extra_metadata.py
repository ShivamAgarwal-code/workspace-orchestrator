"""add gdrive_cache.extra_metadata for content-derived structured hints

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("gdrive_cache", sa.Column("extra_metadata", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("gdrive_cache", "extra_metadata")
