"""brand documents keep the uploaded file

Distillation runs later, from a button, and a scanned page has nothing in the text
layer. The original bytes are what a vision model is shown. Deferred on the model
so listing documents does not read them.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-15 11:40:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("brand_documents", sa.Column("file_bytes", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("brand_documents", "file_bytes")
