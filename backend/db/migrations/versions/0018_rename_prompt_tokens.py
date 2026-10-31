"""rename prompt token columns

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-29 01:55:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("usage_records", "prompt", new_column_name="prompt_tokens")
    op.alter_column("model_calls", "prompt", new_column_name="prompt_tokens")


def downgrade() -> None:
    op.alter_column("usage_records", "prompt_tokens", new_column_name="prompt")
    op.alter_column("model_calls", "prompt_tokens", new_column_name="prompt")
