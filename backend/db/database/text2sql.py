"""Scoped read-only queries and the log of questions that produced them."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select, text

from .. import orm
from .support import _row_dict, utcnow

_READER_ROLE = "text2sql_reader"


class Text2SqlMixin:
    def run_scoped_read(self, sql: str, views: list[tuple[str, str]], params: dict[str, Any]) -> list[dict[str, Any]]:
        """Run one SELECT against TEMP views as `text2sql_reader`, then roll back.

        Each view already filters to the partnership (or its brand). The reader role has
        no grants until this transaction gives it SELECT on those views and USAGE on the
        real temporary schema, so a statement that slipped the parser still cannot write
        or read server files. The rollback drops the views and resets the role. Scope ids
        are inlined as quoted literals: a view body cannot keep a bind parameter. The user
        statement goes through `text()` with colons escaped, so `::cast` stays a cast and
        `%` in a LIKE pattern is not eaten as a driver placeholder.
        """
        statement = (sql or "").strip()
        if not re.match(r"^(select|with)\b", statement, re.IGNORECASE):
            raise ValueError("only a single unqualified SELECT is executed")
        if ";" in statement or re.search(r"\b(public|pg_catalog|information_schema)\s*\.", statement, re.IGNORECASE):
            raise ValueError("only a single unqualified SELECT is executed")
        ident = re.compile(r"^[a-z_][a-z0-9_]*$")
        inlined = [(name, _inline_scope(view_sql, params)) for name, view_sql in views]
        with self.engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.exec_driver_sql("SELECT set_config('statement_timeout', '8000', true)")
                conn.exec_driver_sql("SELECT set_config('lock_timeout', '2000', true)")
                for name, view_sql in inlined:
                    if ident.match(name) is None or ";" in view_sql:
                        raise ValueError(f"refusing view {name}")
                    conn.exec_driver_sql(f'CREATE TEMP VIEW "{name}" AS {view_sql}')
                    conn.exec_driver_sql(f'GRANT SELECT ON "{name}" TO {_READER_ROLE}')
                temp_schema = conn.exec_driver_sql(
                    "SELECT nspname FROM pg_namespace WHERE oid = pg_my_temp_schema()",
                ).scalar()
                if not isinstance(temp_schema, str) or re.fullmatch(r"pg_temp_\d+", temp_schema) is None:
                    raise RuntimeError("temporary schema was not created")
                # `pg_temp` is an alias for the *current role's* temp schema. After SET ROLE
                # it would point somewhere else, so the path is the real schema these views live in.
                conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{temp_schema}" TO {_READER_ROLE}')
                conn.exec_driver_sql(f"SELECT set_config('search_path', '{temp_schema}', true)")
                conn.exec_driver_sql(f"SET LOCAL ROLE {_READER_ROLE}")
                result = conn.execute(text(statement.replace(":", r"\:")))
                keys = list(result.keys())
                return [{key: row._mapping[key] for key in keys} for row in result.fetchmany(200)]
            finally:
                trans.rollback()

    def add_text2sql_log(self, row: dict[str, Any]) -> int:
        with self.tx() as s:
            log = orm.Text2SQLLog(
                partnership_id=row["partnership_id"],
                session_id=row.get("session_id"),
                question=row["question"],
                selected_tables=list(row.get("selected_tables") or []),
                generated_sql=row.get("generated_sql"),
                execution_success=bool(row.get("execution_success")),
                row_count=row.get("row_count"),
                execution_time_ms=row.get("execution_time_ms"),
                error_type=row.get("error_type"),
                error_message=row.get("error_message"),
                user_feedback=row.get("user_feedback"),
                feedback_reason=row.get("feedback_reason"),
                corrected_sql=row.get("corrected_sql"),
                created_at=row.get("created_at") or utcnow(),
            )
            s.add(log)
            s.flush()
            return log.id

    def list_text2sql_logs(self, partnership_id: str | None = None, *, limit: int = 200) -> list[dict[str, Any]]:
        with self.tx() as s:
            q = select(orm.Text2SQLLog).order_by(orm.Text2SQLLog.id.desc()).limit(limit)
            if partnership_id:
                q = q.where(orm.Text2SQLLog.partnership_id == partnership_id)
            return [_row_dict(r) for r in s.scalars(q).all()]

    def set_text2sql_feedback(
        self, id: int, feedback: str, reason: str | None = None, corrected_sql: str | None = None,
        *, partnership_id: str | None = None, session_id: str | None = None,
    ) -> None:
        if feedback not in {"good", "bad"}:
            raise ValueError("feedback must be good or bad")
        with self.tx() as s:
            row = s.get(orm.Text2SQLLog, id)
            if row is None:
                raise KeyError(f"text2sql log {id} not found")
            if partnership_id is not None and row.partnership_id != partnership_id:
                raise KeyError(f"text2sql log {id} not found")
            if session_id is not None and row.session_id not in (None, session_id):
                raise KeyError(f"text2sql log {id} not found")
            row.user_feedback = feedback
            row.feedback_reason = _clip(reason, 500)
            row.corrected_sql = _clip(corrected_sql, 4_000)

    def stamp_text2sql_feedback(self, session_id: str, log_id: int, feedback: str) -> None:
        """Remember the rating on the assistant turn so a reloaded chat still shows it."""
        session = self.session(session_id)
        if not session:
            return
        history = list(session.get("history") or [])
        for message in history:
            mark = message.get("data_query") if isinstance(message, dict) else None
            if isinstance(mark, dict) and mark.get("log_id") == log_id:
                mark["feedback"] = feedback
        self.update_session(session_id, history=history)


def _clip(value: str | None, limit: int) -> str | None:
    text = (value or "").strip()
    return text[:limit] if text else None


def _inline_scope(sql: str, params: dict[str, Any]) -> str:
    """Replace `:name` scope binds with quoted literals. Ids are app-generated tokens."""
    for key, value in params.items():
        token = str(value)
        if re.fullmatch(r"[A-Za-z0-9_\-]+", token) is None:
            raise ValueError("scope id is not a safe token")
        sql = sql.replace(":" + key, "'" + token + "'")
    return sql
