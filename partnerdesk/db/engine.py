"""Engine construction and schema migration.

One engine per `Database`. SQLite gets the pragmas a multi-threaded local app needs
(WAL, a busy timeout, foreign keys ON — SQLite ships with them OFF); a server URL gets a
connection pool instead, so `PARTNERDESK_DB_URL=postgresql+psycopg://…` is a deploy-time
change, not a code change.

Pool size is env-tunable (`PARTNERDESK_POOL_SIZE`, `PARTNERDESK_MAX_OVERFLOW`) because load
testing is exactly the exercise of moving it: the ceiling on concurrent in-flight requests is
`pool_size + max_overflow`, and past it callers queue rather than fail. `pool_pre_ping`
discards connections the server closed under us — without it a restarted or idle-timed-out
Postgres surfaces as a burst of errors in the middle of a run.

The schema is owned by Alembic (`partnerdesk/db/migrations`). `run_migrations` brings a
database to head in-process — the app calls it on startup and the tests on every fresh
file, so there is exactly one definition of the schema and it is the versioned one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event

from ..config import env

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _json_dumps(o: Any) -> str:
    return json.dumps(o, ensure_ascii=False)


def _int_env(name: str, default: int) -> int:
    raw = env(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def make_engine(url: str) -> Engine:
    kwargs: dict[str, Any] = {"json_serializer": _json_dumps, "future": True}
    if url.startswith("sqlite"):
        # Streamlit and FastAPI both hand connections across threads.
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 10}
    else:
        kwargs.update(
            pool_size=_int_env("PARTNERDESK_POOL_SIZE", 10),
            max_overflow=_int_env("PARTNERDESK_MAX_OVERFLOW", 20),
            pool_timeout=_int_env("PARTNERDESK_POOL_TIMEOUT", 30),
            pool_recycle=1800,
            pool_pre_ping=True,
        )
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=10000")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    return engine


def alembic_config(url: str | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    if url:
        cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def run_migrations(engine: Engine) -> None:
    """Upgrade to head using the given engine's connection (so tests and the app migrate
    the very database they are about to use, whatever its URL)."""
    cfg = alembic_config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
