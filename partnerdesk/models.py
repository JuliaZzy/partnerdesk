"""The data the agent reads and writes — only the fields it actually touches.

See docs/porting/04-data-model.md for where each field came from and why the rest of the
first version's tables were left behind.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PaymentTerms = Literal["50_50", "100_prepaid", "net_30", "net_60"]
Incoterms = Literal["FOB", "CIF", "EXW", "DDP", "DAP", "CFR"]
ShippingMethod = Literal["sea", "air", "express", "land"]

PAYMENT_TERMS_VALUES: tuple[str, ...] = ("50_50", "100_prepaid", "net_30", "net_60")
INCOTERMS_VALUES: tuple[str, ...] = ("FOB", "CIF", "EXW", "DDP", "DAP", "CFR")
SHIPPING_METHOD_VALUES: tuple[str, ...] = ("sea", "air", "express", "land")


class Product(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    sku: str
    product_name_en: str | None = None
    native_name: str | None = None
    category: str | None = None
    subcategory: str | None = None
    variant: str | None = None
    form: str | None = None
    unit_size: str | None = None
    net_content: str | None = None
    description_en: str | None = None
    case_pack: int | None = None
    case_price: float | None = None
    moq_units: int | None = None
    cases_per_layer: int | None = None
    layers_per_pallet: int | None = None
    cases_per_pallet: int | None = None
    case_weight: float | None = None
    commercial_role: str | None = None  # gift | sample | regular | ...
    discontinued: bool = False
    policy_tags: list[str] = Field(default_factory=list)
    restricted_territories: list[str] = Field(default_factory=list)

    @property
    def display_name(self) -> str:
        return self.product_name_en or self.native_name or self.sku


class CommercialRule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    rule_type: str
    rule_config: dict[str, Any] = Field(default_factory=dict)
    severity: Literal["warn", "approval_required", "block"] = "block"
    is_active: bool = True
    partner_scope: Literal["all", "specific", "region"] = "all"
    partner_ids: list[str] = Field(default_factory=list)
    partner_regions: list[str] = Field(default_factory=list)
    product_scope: Literal["all", "specific", "role", "tag"] = "all"
    product_ids: list[str] = Field(default_factory=list)
    product_roles: list[str] = Field(default_factory=list)
    product_tags: list[str] = Field(default_factory=list)
    effective_from: str | None = None  # YYYY-MM-DD, inclusive
    effective_until: str | None = None


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


class Partnership(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    brand_id: str
    distributor_id: str
    brand_name: str
    distributor_name: str
    ship_to_country: str | None = None
    currency: str = "USD"


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
    submitted_at: str | None = None
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
