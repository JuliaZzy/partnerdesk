"""The data the agent reads and writes — only the fields it actually touches.

See docs/porting/04-data-model.md for where each field came from and why the rest of the
first version's tables were left behind.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


# --- contracts -------------------------------------------------------------------
# What `legacy/contract_ai` reads out of an agreement, kept in full: the raw extraction
# (audit, re-mapping) plus these normalized terms the agent acts on. Discount tiers are the
# reason this exists — a contract's discount is a schedule, not one number.


class ContractDiscountTier(BaseModel):
    model_config = ConfigDict(extra="ignore")

    position: int = 0
    from_container: int | None = None  # 1-based; None = from the first
    to_container: int | None = None  # inclusive; None = "and all further"
    discount_percent: float | None = None  # 0 = list price for this band
    notes: str | None = None

    def covers(self, n: int) -> bool:
        lo = self.from_container or 1
        return n >= lo and (self.to_container is None or n <= self.to_container)


class ContractDiscountRule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    position: int = 0
    title: str | None = None
    section_reference: str | None = None
    applies_per: str | None = None  # each_contract_year | calendar_year | per_order | …
    basis: str | None = None  # shipping_container_sequence | purchase_volume | …
    source_clause_text: str | None = None
    tiers: list[ContractDiscountTier] = Field(default_factory=list)

    @property
    def max_percent(self) -> float | None:
        pcts = [t.discount_percent for t in self.tiers if t.discount_percent is not None and t.discount_percent > 0]
        return max(pcts) if pcts else None


class ContractMoq(BaseModel):
    model_config = ConfigDict(extra="ignore")

    quantity: float | None = None
    unit: str | None = None
    applies_per: str | None = None
    product_scope: str | None = None
    sku: str | None = None
    currency: str | None = None
    source_clause_text: str | None = None


class ContractPriceEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    product_name: str | None = None
    product_sku: str | None = None
    unit_price: float | None = None
    currency: str | None = None
    unit: str | None = None
    list_price_reference: str | None = None
    product_specific_discount_text: str | None = None
    source_clause_text: str | None = None


class ContractTerritory(BaseModel):
    model_config = ConfigDict(extra="ignore")

    country_code: str
    kind: Literal["allowed", "excluded"]


class ContractEvidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    field: str
    quote: str
    page: int | None = None
    section_hint: str | None = None
    char_start: int | None = None


class ContractTerms(BaseModel):
    discount_rules: list[ContractDiscountRule] = Field(default_factory=list)
    moqs: list[ContractMoq] = Field(default_factory=list)
    price_list: list[ContractPriceEntry] = Field(default_factory=list)
    territories: list[ContractTerritory] = Field(default_factory=list)
    evidence: list[ContractEvidence] = Field(default_factory=list)


class Contract(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    brand_id: str
    partnership_id: str
    title: str | None = None
    file_name: str | None = None
    file_sha256: str | None = None
    contract_type: str | None = None
    status: Literal["active", "superseded"] = "active"
    effective_from: str | None = None  # YYYY-MM-DD
    term_start_date: str | None = None
    term_end_date: str | None = None
    auto_renewal: bool | None = None
    exclusivity_type: str | None = None
    territory_text: str | None = None
    annual_sales_target: dict[str, Any] | None = None
    annual_sales_target_currency: str | None = None
    created_at: datetime | None = None


class ContractRecord(BaseModel):
    """Everything one ingestion writes, atomically: the contract, its raw extraction, the
    normalized terms, and what was derived from them for the PO gate."""

    contract: Contract
    extraction_raw: dict[str, Any]
    model: str | None = None
    confidence_score: float | None = None
    extraction_notes: str | None = None
    skipped: list[str] = Field(default_factory=list)
    terms: ContractTerms = Field(default_factory=ContractTerms)
    derived_rules: list[CommercialRule] = Field(default_factory=list)
    product_moq_units: dict[str, int] = Field(default_factory=dict)  # sku → units


# --- brand knowledge base ----------------------------------------------------------
# `legacy/brand_knowledge` distils a brand's decks and catalogues into typed fragments. A
# fragment is a self-contained thing the agent may say about the brand or a product; it
# is draft until a person approves it. The type is an open slug (see knowledge.SEED_TYPES).


class BrandDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    brand_id: str
    title: str | None = None
    file_name: str
    file_sha256: str | None = None
    media_type: str | None = None
    size_bytes: int | None = None
    page_count: int | None = None
    pages_processed: int | None = None
    summary: str | None = None
    model: str | None = None
    status: Literal["uploaded", "distilled", "failed"] = "uploaded"
    distilled_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DocumentSection(BaseModel):
    """One kind of content the reader found in a document (pass 1 of the distillation)."""

    model_config = ConfigDict(extra="ignore")

    type: str
    label: str
    description: str | None = None
    page_numbers: list[int] = Field(default_factory=list)


class KnowledgeFragment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    brand_id: str
    document_id: str | None = None
    product_id: str | None = None
    type: str
    title: str
    content: str
    product_name: str | None = None
    source_pages: list[int] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    status: Literal["draft", "approved", "archived"] = "draft"
    created_at: datetime | None = None
    updated_at: datetime | None = None


# --- sales reports -----------------------------------------------------------------
# A fact is one number (or one prose block) read out of a report at native granularity,
# with where it came from. See docs/porting/06-report-core.md for the shape's origin.


class SalesReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    brand_id: str
    partnership_id: str
    title: str | None = None
    file_name: str
    file_sha256: str | None = None
    media_type: str | None = None
    size_bytes: int | None = None
    reporting_period: str | None = None  # YYYY-MM | YYYY-Qn | YYYY
    period_start: str | None = None
    period_end: str | None = None
    status: Literal["draft", "confirmed"] = "draft"
    confirmed_at: datetime | None = None
    notes: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ReportFact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    report_id: str | None = None
    extraction_id: str | None = None
    metric_key: str
    fact_type: str
    channel: str | None = None
    sku: str | None = None
    product_id: str | None = None
    entity: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    numeric_value: float | None = None
    text_value: str | None = None
    unit: str | None = None
    currency: str | None = None
    source_sheet: str | None = None
    source_row: int | None = None
    source_col: int | None = None
    source_header: str | None = None
    ai_derived: bool = False
    status: Literal["draft", "confirmed", "superseded"] = "draft"
    created_at: datetime | None = None

    def identity(self) -> tuple[Any, ...]:
        """What makes two facts 'the same measurement' — a re-import supersedes on this key."""
        return (self.metric_key, self.fact_type, self.channel, self.sku, self.entity, self.period_start, self.period_end)


# --- long-term memory --------------------------------------------------------------


class Memory(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    partnership_id: str
    kind: Literal["preference", "fact", "instruction", "event"] = "fact"
    content: str
    source: Literal["user", "chat", "system"] = "user"
    session_id: str | None = None
    is_active: bool = True
    last_recalled_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
