"""chat write-ups confirmed into narrative_reports

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-23 00:30:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "narrative_reports",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("brand_id", sa.String(length=64), nullable=False),
        sa.Column("partnership_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("charts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["brand_id"], ["brands.id"]),
        sa.ForeignKeyConstraint(["partnership_id"], ["partnerships.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_narrative_reports_brand_id", "narrative_reports", ["brand_id"])
    op.create_index(
        "ix_narrative_reports_partnership_updated",
        "narrative_reports",
        ["partnership_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_narrative_reports_partnership_updated", table_name="narrative_reports")
    op.drop_index("ix_narrative_reports_brand_id", table_name="narrative_reports")
    op.drop_table("narrative_reports")
