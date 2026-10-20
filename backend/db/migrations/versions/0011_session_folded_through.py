"""sessions remember how much of the transcript is already long-term memory

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-20 01:40:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("folded_through", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("sessions", "folded_through", server_default=None)


def downgrade() -> None:
    op.drop_column("sessions", "folded_through")
