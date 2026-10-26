"""people at a distributor; chats and memories belong to one of them

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-26 07:22:28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("distributor_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["distributor_id"], ["distributors.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_distributor_id", "users", ["distributor_id"])
    op.add_column("memories", sa.Column("user_id", sa.String(length=64), nullable=True))
    op.create_index("ix_memories_user_id", "memories", ["user_id"])
    op.create_foreign_key("memories_user_id_fkey", "memories", "users", ["user_id"], ["id"], ondelete="SET NULL")
    op.add_column("sessions", sa.Column("user_id", sa.String(length=64), nullable=True))
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_foreign_key("sessions_user_id_fkey", "sessions", "users", ["user_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("sessions_user_id_fkey", "sessions", type_="foreignkey")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_column("sessions", "user_id")
    op.drop_constraint("memories_user_id_fkey", "memories", type_="foreignkey")
    op.drop_index("ix_memories_user_id", table_name="memories")
    op.drop_column("memories", "user_id")
    op.drop_index("ix_users_distributor_id", table_name="users")
    op.drop_table("users")
