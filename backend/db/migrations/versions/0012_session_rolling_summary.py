"""sessions keep one rolling summary of the turns already folded

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-20 09:22:05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("summary", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "summary")
