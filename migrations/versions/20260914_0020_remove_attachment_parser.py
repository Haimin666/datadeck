"""Remove the unused attachment parser branch.

Revision ID: 20260914_0020
Revises: 20260914_0019
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260914_0020"
down_revision: str | None = "20260914_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Keep thread attachments as plain files read by the runtime tool."""
    op.execute("ALTER TABLE thread_attachments DROP COLUMN IF EXISTS parsed_object_name")
    op.execute("ALTER TABLE thread_attachments DROP COLUMN IF EXISTS parse_method")


def downgrade() -> None:
    op.add_column(
        "thread_attachments",
        sa.Column("parsed_object_name", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "thread_attachments",
        sa.Column("parse_method", sa.String(length=32), nullable=True),
    )
