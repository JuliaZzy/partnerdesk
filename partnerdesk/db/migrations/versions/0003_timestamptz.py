"""timestamps: ISO-8601 text -> timestamptz (see partnerdesk/db/orm.py)

Every `_at` column across the schema in one step. The old values were written by
`datetime.now(UTC).isoformat(timespec="seconds")`, so they all carry an offset and
Postgres parses them without ambiguity; `USING col::timestamptz` does the whole
conversion in place. Plain `YYYY-MM-DD` date columns are untouched — they are calendar
labels, not instants.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-23 15:50:53.464613
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

# (table, columns) — the nullable ones are listed separately below so `alter_column` keeps
# each column's constraint exactly as it was.
TIMESTAMP_COLUMNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("brand_agent_settings", ("updated_at",)),
    ("brand_documents", ("distilled_at", "created_at", "updated_at")),
    ("brands", ("created_at", "updated_at")),
    ("commercial_rules", ("created_at", "updated_at")),
    ("contract_extractions", ("extracted_at",)),
    ("contracts", ("created_at", "updated_at")),
    ("distributors", ("created_at", "updated_at")),
    ("knowledge_fragments", ("created_at", "updated_at")),
    ("memories", ("last_recalled_at", "created_at", "updated_at")),
    ("partnerships", ("created_at", "updated_at")),
    ("po_events", ("created_at",)),
    ("po_rule_evaluations", ("created_at",)),
    ("products", ("created_at", "updated_at")),
    ("purchase_orders", ("submitted_at", "created_at", "updated_at")),
    ("report_extractions", ("extracted_at",)),
    ("report_facts", ("created_at",)),
    ("sales_reports", ("confirmed_at", "created_at", "updated_at")),
    ("sessions", ("confirm_expires_at", "confirmed_at", "created_at", "updated_at")),
)

NULLABLE: frozenset[tuple[str, str]] = frozenset({
    ("brand_documents", "distilled_at"),
    ("memories", "last_recalled_at"),
    ("purchase_orders", "submitted_at"),
    ("sales_reports", "confirmed_at"),
    ("sessions", "confirm_expires_at"),
    ("sessions", "confirmed_at"),
})

# Back to exactly what the code used to write: `isoformat(timespec="seconds")` on a
# UTC-aware datetime, e.g. 2026-09-23T15:50:53+00:00 — 25 characters, inside String(32).
TO_ISO_TEXT = """to_char({col} at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS') || '+00:00'"""


def upgrade() -> None:
    for table, columns in TIMESTAMP_COLUMNS:
        for col in columns:
            op.alter_column(
                table, col,
                existing_type=sa.VARCHAR(length=32),
                type_=sa.DateTime(timezone=True),
                existing_nullable=(table, col) in NULLABLE,
                postgresql_using=f"{col}::timestamptz",
            )


def downgrade() -> None:
    for table, columns in TIMESTAMP_COLUMNS:
        for col in columns:
            op.alter_column(
                table, col,
                existing_type=sa.DateTime(timezone=True),
                type_=sa.VARCHAR(length=32),
                existing_nullable=(table, col) in NULLABLE,
                postgresql_using=TO_ISO_TEXT.format(col=col),
            )
