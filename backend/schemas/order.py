"""A submitted purchase order and its lines. Chat gathers slots; this is the row."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PurchaseOrder(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    po_number: str
    brand_id: str
    distributor_id: str
    partnership_id: str
    status: str
    currency: str
    payment_terms: str
    incoterms: str
    shipping_method: str
    eta_date: str | None
    ship_to_country: str | None
    notes: str | None
    discount_amount: float | None
    discount_percentage: float | None
    subtotal_amount: float
    total_amount: float
    submitted_at: datetime | None = None
    submit_cycle_version: int = 0
    prepaid_amount: float = 0.0
    balance_paid: float = 0.0


class PurchaseOrderItem(BaseModel):
    po_id: str
    product_id: str
    sku: str
    product_name: str
    quantity: int
    case_price: float
    line_total: float
    case_pack: int | None
