"""A login-less role for text-to-SQL. The statement runs as this role, which has no
grants of its own — it can read only the TEMP views the request grants it.

CREATE ROLE cannot run inside Alembic's transaction, so the create step uses a
second AUTOCOMMIT connection. The role is cluster-wide: a later database skips
the create and only grants membership to the user migrating it.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-19 02:30:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_ROLE = "text2sql_reader"


def upgrade() -> None:
    bind = op.get_bind()
    exists = bind.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": _ROLE}).scalar()
    if not exists:
        # CREATE ROLE cannot run inside the migration transaction. A second
        # connection in AUTOCOMMIT does it without committing the migration early.
        with bind.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql(
                f"CREATE ROLE {_ROLE} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT",
            )
    op.execute(f"GRANT {_ROLE} TO CURRENT_USER")


def downgrade() -> None:
    op.execute(f"REVOKE {_ROLE} FROM CURRENT_USER")
