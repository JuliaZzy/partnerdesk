"""contract heading tree for reference navigation

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-15 19:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contract_chunks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("contract_id", sa.String(length=64), nullable=False),
        sa.Column("chunk_id", sa.String(length=16), nullable=False),
        sa.Column("parent_chunk_id", sa.String(length=16), nullable=True),
        sa.Column("number", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("path", sa.String(length=500), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("sibling_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("contract_id", "chunk_id", name="uq_contract_chunk"),
    )
    op.create_index(op.f("ix_contract_chunks_contract_id"), "contract_chunks", ["contract_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_contract_chunks_contract_id"), table_name="contract_chunks")
    op.drop_table("contract_chunks")
