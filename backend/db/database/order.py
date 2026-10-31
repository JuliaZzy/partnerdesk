"""Purchase orders, their lines, and the events recorded against them."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select

from ...schemas import PurchaseOrder
from .. import orm
from .support import _row_dict, utcnow


class OrderMixin:
    def insert_po(self, po: PurchaseOrder, items: list[dict[str, Any]], rule_snapshot: list[dict[str, Any]]) -> None:
        ts = utcnow()
        with self.tx() as s:
            s.add(orm.PurchaseOrder(**po.model_dump(), created_at=ts, updated_at=ts))
            s.flush()  # items and the snapshot reference it
            s.add_all(orm.PurchaseOrderItem(po_id=po.id, **{k: i[k] for k in ("product_id", "sku", "product_name", "quantity", "case_price", "line_total", "case_pack")}) for i in items)
            if rule_snapshot:
                s.add(orm.PoRuleEvaluation(po_id=po.id, evaluation_context="submission", submit_cycle_version=po.submit_cycle_version, snapshot=rule_snapshot, created_at=ts))

    def po(self, id: str) -> PurchaseOrder | None:
        with self.tx() as s:
            row = s.get(orm.PurchaseOrder, id)
            return PurchaseOrder(**_row_dict(row)) if row else None

    def po_items(self, id: str) -> list[dict[str, Any]]:
        with self.tx() as s:
            return [_row_dict(r) for r in s.scalars(select(orm.PurchaseOrderItem).where(orm.PurchaseOrderItem.po_id == id).order_by(orm.PurchaseOrderItem.id))]

    def list_pos(self, partnership_id: str | None = None) -> list[PurchaseOrder]:
        with self.tx() as s:
            q = select(orm.PurchaseOrder)
            if partnership_id:
                q = q.where(orm.PurchaseOrder.partnership_id == partnership_id)
            return [PurchaseOrder(**_row_dict(r)) for r in s.scalars(q.order_by(orm.PurchaseOrder.created_at.desc(), orm.PurchaseOrder.po_number.desc()))]

    def count_submitted_pos(self, partnership_id: str, since: date | None = None) -> int:
        """Orders this partnership has placed (submitted_at set), optionally from a day.

        `since` is a calendar date and the column an instant, so the bound is that day's
        UTC midnight — the same cut the ISO-text comparison used to make lexically."""
        with self.tx() as s:
            q = select(func.count()).select_from(orm.PurchaseOrder).where(
                orm.PurchaseOrder.partnership_id == partnership_id, orm.PurchaseOrder.submitted_at.is_not(None),
            )
            if since:
                q = q.where(orm.PurchaseOrder.submitted_at >= datetime(since.year, since.month, since.day, tzinfo=UTC))
            return int(s.scalar(q) or 0)

    def update_po(self, id: str, **fields: Any) -> None:
        with self.tx() as s:
            row = s.get(orm.PurchaseOrder, id)
            if row is None:
                raise KeyError(f"purchase order {id} not found")
            for k, v in fields.items():
                setattr(row, k, v)
            row.updated_at = utcnow()

    def add_po_event(self, po_id: str, kind: str, data: dict[str, Any]) -> None:
        with self.tx() as s:
            s.add(orm.PoEvent(po_id=po_id, kind=kind, data=data, created_at=utcnow()))

    def po_events(self, po_id: str) -> list[dict[str, Any]]:
        with self.tx() as s:
            rows = s.scalars(select(orm.PoEvent).where(orm.PoEvent.po_id == po_id).order_by(orm.PoEvent.id)).all()
            return [{"kind": r.kind, "data": r.data, "created_at": r.created_at} for r in rows]
