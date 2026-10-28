"""Tables for `schemas/order.py`.

`schemas/po.py` is the draft the chat fills in. Once submitted, the order and its
review trail live here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, JSONCol, TimestampCol


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    po_number: Mapped[str] = mapped_column(String(32), unique=True)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    distributor_id: Mapped[str] = mapped_column(ForeignKey("distributors.id"), index=True)
    partnership_id: Mapped[str] = mapped_column(ForeignKey("partnerships.id"), index=True)
    status: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(3))
    payment_terms: Mapped[str] = mapped_column(String(16))
    incoterms: Mapped[str] = mapped_column(String(8))
    shipping_method: Mapped[str] = mapped_column(String(16))
    eta_date: Mapped[str | None] = mapped_column(String(10))
    ship_to_country: Mapped[str | None] = mapped_column(String(2))
    notes: Mapped[str | None] = mapped_column(Text)
    discount_amount: Mapped[float | None] = mapped_column(Float)
    discount_percentage: Mapped[float | None] = mapped_column(Float)
    subtotal_amount: Mapped[float] = mapped_column(Float)
    total_amount: Mapped[float] = mapped_column(Float)
    submitted_at: Mapped[datetime | None] = mapped_column(TimestampCol)
    submit_cycle_version: Mapped[int] = mapped_column(Integer, default=0)
    prepaid_amount: Mapped[float] = mapped_column(Float, default=0.0)
    balance_paid: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(TimestampCol)
    updated_at: Mapped[datetime] = mapped_column(TimestampCol)


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    po_id: Mapped[str] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    sku: Mapped[str] = mapped_column(String(64))
    product_name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(Integer)
    case_price: Mapped[float] = mapped_column(Float)
    line_total: Mapped[float] = mapped_column(Float)
    case_pack: Mapped[int | None] = mapped_column(Integer)


class PoRuleEvaluation(Base):
    """Snapshot of every rule result at submission — what the approver reviews."""

    __tablename__ = "po_rule_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    po_id: Mapped[str] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True)
    evaluation_context: Mapped[str] = mapped_column(String(32))
    submit_cycle_version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONCol)
    created_at: Mapped[datetime] = mapped_column(TimestampCol)


class PoEvent(Base):
    __tablename__ = "po_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    po_id: Mapped[str] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict[str, Any]] = mapped_column(JSONCol, default=dict)
    created_at: Mapped[datetime] = mapped_column(TimestampCol)
