"""sessions get title + tags for the chat list

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-15 15:20:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("title", sa.String(length=120), nullable=True))
    op.add_column(
        "sessions",
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.alter_column("sessions", "tags", server_default=None)


def downgrade() -> None:
    op.drop_column("sessions", "tags")
    op.drop_column("sessions", "title")
