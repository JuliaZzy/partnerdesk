"""text2sql attempt log for bad-case review

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-19 02:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "text2sql_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("partnership_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("selected_tables", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("generated_sql", sa.Text(), nullable=True),
        sa.Column("execution_success", sa.Boolean(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("execution_time_ms", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("user_feedback", sa.String(length=20), nullable=True),
        sa.Column("feedback_reason", sa.Text(), nullable=True),
        sa.Column("corrected_sql", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["partnership_id"], ["partnerships.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_text2sql_logs_partnership_created", "text2sql_logs", ["partnership_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_text2sql_logs_partnership_created", table_name="text2sql_logs")
    op.drop_table("text2sql_logs")
