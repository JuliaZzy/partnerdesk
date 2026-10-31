"""Chat sessions. Confirm tokens are burned here, before an order is created."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, update

from .. import orm
from .support import _history_ids, _row_dict, utcnow


class SessionMixin:
    def create_session(self, id: str, partnership_id: str, user_id: str | None = None) -> dict[str, Any]:
        ts = utcnow()
        with self.tx() as s:
            s.add(orm.ChatSession(
                id=id, partnership_id=partnership_id, user_id=user_id, title=None, tags=[], slots={}, history=[],
                created_at=ts, updated_at=ts,
            ))
        return self.session(id)  # type: ignore[return-value]

    def session(self, id: str) -> dict[str, Any] | None:
        with self.tx() as s:
            row = s.get(orm.ChatSession, id)
            return _row_dict(row) if row else None

    def list_sessions(self, partnership_id: str, limit: int = 50, user_id: str | None = None) -> list[dict[str, Any]]:
        """A partnership's chats, most recently touched first (the sidebar's chat list).

        Pass `user_id` to see one person's chats. Omit it and every chat on the partnership comes back.
        """
        with self.tx() as s:
            q = select(orm.ChatSession).where(orm.ChatSession.partnership_id == partnership_id)
            if user_id is not None:
                q = q.where(orm.ChatSession.user_id == user_id)
            rows = s.scalars(
                q.order_by(orm.ChatSession.updated_at.desc(), orm.ChatSession.created_at.desc()).limit(limit)
            ).all()
            return [_row_dict(r) for r in rows]

    def update_session(self, id: str, **fields: Any) -> None:
        with self.tx() as s:
            row = s.get(orm.ChatSession, id)
            if row is None:
                raise KeyError(f"session {id} not found")
            for k, v in fields.items():
                setattr(row, k, v)
            row.updated_at = utcnow()

    def delete_session(self, id: str) -> None:
        """Remove the chat. What it filed stays: the order, the agreement, the write-up.

        A memory or query log that pointed here keeps its row and loses the link.
        The order stays because `po_id` points from the chat at the order, not the other way.
        """
        with self.tx() as s:
            row = s.get(orm.ChatSession, id)
            if row is None:
                raise KeyError(f"session {id} not found")
            s.delete(row)

    def purge_session(self, id: str) -> None:
        """Remove the chat and the records it filed.

        That is the order on `po_id`, agreements named in the transcript, and write-ups
        tied to this chat. Rules derived from those agreements go with them. A memory or
        query log keeps its row and loses the link. Another chat's records stay.
        """
        with self.tx() as s:
            row = s.get(orm.ChatSession, id)
            if row is None:
                raise KeyError(f"session {id} not found")
            po_id = row.po_id
            contract_ids = _history_ids(row.history, "contract_id")
            reports = s.scalars(select(orm.NarrativeReport).where(orm.NarrativeReport.session_id == id))
            for report in reports:
                s.delete(report)
            if contract_ids:
                rules = s.scalars(select(orm.CommercialRule).where(orm.CommercialRule.contract_id.in_(contract_ids)))
                for rule in rules:
                    s.delete(rule)
                contracts = s.scalars(select(orm.Contract).where(orm.Contract.id.in_(contract_ids)))
                for contract in contracts:
                    s.delete(contract)
            # The chat points at the order. Clear that before the order row goes.
            if po_id:
                row.po_id = None
                s.flush()
                po = s.get(orm.PurchaseOrder, po_id)
                if po is not None:
                    s.delete(po)
            s.delete(row)

    def burn_confirm_token(self, token: str) -> str | None:
        """Atomically consume a confirm token. Returns the session id, or None when the
        token was already used — two clicks landing together see one UPDATE match and one
        match nothing. Done BEFORE the order is created, never after."""
        ts = utcnow()
        with self.tx() as s:
            r = s.execute(
                update(orm.ChatSession).where(orm.ChatSession.confirm_token == token)
                .values(confirm_token=None, confirmed_at=ts, updated_at=ts).returning(orm.ChatSession.id)
            ).first()
            return r[0] if r else None

    def session_by_token(self, token: str) -> dict[str, Any] | None:
        with self.tx() as s:
            row = s.scalars(select(orm.ChatSession).where(orm.ChatSession.confirm_token == token)).first()
            return _row_dict(row) if row else None
