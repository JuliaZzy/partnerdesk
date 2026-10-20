"""Brand-level terms the desk fills in when the distributor has not said otherwise."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

PaymentTerms = Literal["50_50", "100_prepaid", "net_30", "net_60"]
Incoterms = Literal["FOB", "CIF", "EXW", "DDP", "DAP", "CFR"]
ShippingMethod = Literal["sea", "air", "express", "land"]

PAYMENT_TERMS_VALUES: tuple[str, ...] = ("50_50", "100_prepaid", "net_30", "net_60")
# What each code means — the semantics claims.py settles money by. Spelled out wherever a
# model might otherwise improvise ("50/50" was read back to a customer as "50% before shipment").
PAYMENT_TERMS_LABELS: dict[str, str] = {
    "50_50": "50% prepaid with the order, 50% balance after the goods are received and inspected",
    "100_prepaid": "100% prepaid with the order",
    "net_30": "payable 30 days after receipt",
    "net_60": "payable 60 days after receipt",
}
INCOTERMS_VALUES: tuple[str, ...] = ("FOB", "CIF", "EXW", "DDP", "DAP", "CFR")
SHIPPING_METHOD_VALUES: tuple[str, ...] = ("sea", "air", "express", "land")


class AgentSettings(BaseModel):
    """Per-brand knobs, resolved against the constants the code used before they existed —
    a missing row and a row full of NULLs must read identically."""

    model_config = ConfigDict(extra="ignore")

    payment_terms: PaymentTerms = "50_50"
    incoterms: Incoterms = "CIF"
    shipping_method: ShippingMethod = "sea"
    eta_days: int = 60
    operating_context: str | None = None
    min_po_confidence: float = 0.0
    tone: str = "Warm, direct and professional. Short sentences. No filler."

    @field_validator("operating_context")
    @classmethod
    def _cap_context(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        return v[:2000] if v else None
