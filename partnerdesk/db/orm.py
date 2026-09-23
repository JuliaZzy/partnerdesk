"""The relational schema — one SQLAlchemy class per table.

Everything the agent reads or writes lives here as real columns with keys, so it can be
queried, joined and migrated. The only JSON columns are the ones whose shape is genuinely
polymorphic (a rule's `rule_config` depends on its `rule_type`; a contract extraction is
kept verbatim next to its normalized rows so it can be audited or re-mapped later).

Timestamps are ISO-8601 text: that is SQLite's own convention, it sorts correctly, and
every caller already compares them with `datetime.fromisoformat`. Dates are `YYYY-MM-DD`.

Naming: these are rows, referred to as `orm.Product` etc. The pydantic models the rest of
the code passes around are in `partnerdesk/models.py`; `database.py` converts between the
two.
"""

from __future__ import annotations

from typing import Any, ClassVar

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# JSON on SQLite, JSONB on Postgres — the same column definition works on both.
JSONCol = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    type_annotation_map: ClassVar = {dict[str, Any]: JSONCol, list[str]: JSONCol, list[dict[str, Any]]: JSONCol}


# --- parties -------------------------------------------------------------------

class Brand(Base):
    __tablename__ = "brands"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    preferred_currency: Mapped[str] = mapped_column(String(3), default="USD")
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))


class Distributor(Base):
    __tablename__ = "distributors"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    country: Mapped[str | None] = mapped_column(String(2))
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))


class Partnership(Base):
    __tablename__ = "partnerships"
    __table_args__ = (UniqueConstraint("brand_id", "distributor_id", name="uq_partnership_pair"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    distributor_id: Mapped[str] = mapped_column(ForeignKey("distributors.id"), index=True)
    ship_to_country: Mapped[str | None] = mapped_column(String(2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))

    brand: Mapped[Brand] = relationship(lazy="joined")
    distributor: Mapped[Distributor] = relationship(lazy="joined")


# --- brand reference data --------------------------------------------------------

class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("brand_id", "sku", name="uq_product_sku_per_brand"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    sku: Mapped[str] = mapped_column(String(64))
    product_name_en: Mapped[str | None] = mapped_column(String(200))
    native_name: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(100))
    subcategory: Mapped[str | None] = mapped_column(String(100))
    variant: Mapped[str | None] = mapped_column(String(100))
    form: Mapped[str | None] = mapped_column(String(100))
    unit_size: Mapped[str | None] = mapped_column(String(50))
    net_content: Mapped[str | None] = mapped_column(String(50))
    description_en: Mapped[str | None] = mapped_column(Text)
    case_pack: Mapped[int | None] = mapped_column(Integer)
    case_price: Mapped[float | None] = mapped_column(Float)
    moq_units: Mapped[int | None] = mapped_column(Integer)
    cases_per_layer: Mapped[int | None] = mapped_column(Integer)
    layers_per_pallet: Mapped[int | None] = mapped_column(Integer)
    cases_per_pallet: Mapped[int | None] = mapped_column(Integer)
    case_weight: Mapped[float | None] = mapped_column(Float)
    commercial_role: Mapped[str | None] = mapped_column(String(32))
    discontinued: Mapped[bool] = mapped_column(Boolean, default=False)
    policy_tags: Mapped[list[str]] = mapped_column(JSONCol, default=list)
    restricted_territories: Mapped[list[str]] = mapped_column(JSONCol, default=list)
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))


class CommercialRule(Base):
    __tablename__ = "commercial_rules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    # Set when the rule was derived from a contract — provenance an approver can follow.
    contract_id: Mapped[str | None] = mapped_column(ForeignKey("contracts.id", ondelete="SET NULL"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    rule_type: Mapped[str] = mapped_column(String(64))
    rule_config: Mapped[dict[str, Any]] = mapped_column(JSONCol, default=dict)
    severity: Mapped[str] = mapped_column(String(32), default="block")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    partner_scope: Mapped[str] = mapped_column(String(16), default="all")
    partner_ids: Mapped[list[str]] = mapped_column(JSONCol, default=list)
    partner_regions: Mapped[list[str]] = mapped_column(JSONCol, default=list)
    product_scope: Mapped[str] = mapped_column(String(16), default="all")
    product_ids: Mapped[list[str]] = mapped_column(JSONCol, default=list)
    product_roles: Mapped[list[str]] = mapped_column(JSONCol, default=list)
    product_tags: Mapped[list[str]] = mapped_column(JSONCol, default=list)
    effective_from: Mapped[str | None] = mapped_column(String(10))
    effective_until: Mapped[str | None] = mapped_column(String(10))
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))


class BrandAgentSettings(Base):
    __tablename__ = "brand_agent_settings"

    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), primary_key=True)
    payment_terms: Mapped[str | None] = mapped_column(String(16))
    incoterms: Mapped[str | None] = mapped_column(String(8))
    shipping_method: Mapped[str | None] = mapped_column(String(16))
    eta_days: Mapped[int | None] = mapped_column(Integer)
    operating_context: Mapped[str | None] = mapped_column(Text)
    min_po_confidence: Mapped[float | None] = mapped_column(Float)
    tone: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(String(32))


# --- contracts ---------------------------------------------------------------------
# One `contracts` row per agreement; every extraction of it is kept verbatim in
# `contract_extractions`, and the terms the agent acts on are normalized into the child
# tables below. Rules derived from a contract point back at it via `commercial_rules.contract_id`.

class Contract(Base):
    __tablename__ = "contracts"
    __table_args__ = (Index("ix_contracts_partnership_status", "partnership_id", "status"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    partnership_id: Mapped[str] = mapped_column(ForeignKey("partnerships.id"))
    title: Mapped[str | None] = mapped_column(String(200))
    file_name: Mapped[str | None] = mapped_column(String(255))
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    contract_type: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | superseded
    effective_from: Mapped[str | None] = mapped_column(String(10))
    term_start_date: Mapped[str | None] = mapped_column(String(10))
    term_end_date: Mapped[str | None] = mapped_column(String(10))
    auto_renewal: Mapped[bool | None] = mapped_column(Boolean)
    exclusivity_type: Mapped[str | None] = mapped_column(String(64))
    territory_text: Mapped[str | None] = mapped_column(Text)
    annual_sales_target: Mapped[dict[str, Any] | None] = mapped_column(JSONCol)
    annual_sales_target_currency: Mapped[str | None] = mapped_column(String(8))
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))

    discount_rules: Mapped[list[ContractDiscountRule]] = relationship(
        back_populates="contract", cascade="all, delete-orphan", order_by="ContractDiscountRule.position",
    )


class ContractExtraction(Base):
    """A run of the contract reader over the agreement — the raw output, verbatim."""

    __tablename__ = "contract_extractions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    model: Mapped[str | None] = mapped_column(String(100))
    confidence_score: Mapped[float | None] = mapped_column(Float)
    extraction_notes: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONCol)
    # What the mapping could not place — reported, never silently dropped.
    skipped: Mapped[list[str]] = mapped_column(JSONCol, default=list)
    extracted_at: Mapped[str] = mapped_column(String(32))


class ContractDiscountRule(Base):
    __tablename__ = "contract_discount_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str | None] = mapped_column(String(200))
    section_reference: Mapped[str | None] = mapped_column(String(100))
    applies_per: Mapped[str | None] = mapped_column(String(32))  # each_contract_year | calendar_year | per_order | …
    basis: Mapped[str | None] = mapped_column(String(32))  # shipping_container_sequence | purchase_volume | …
    source_clause_text: Mapped[str | None] = mapped_column(Text)

    contract: Mapped[Contract] = relationship(back_populates="discount_rules")
    tiers: Mapped[list[ContractDiscountTier]] = relationship(
        back_populates="rule", cascade="all, delete-orphan", order_by="ContractDiscountTier.position", lazy="selectin",
    )


class ContractDiscountTier(Base):
    __tablename__ = "contract_discount_tiers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("contract_discount_rules.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    from_container: Mapped[int | None] = mapped_column(Integer)
    to_container: Mapped[int | None] = mapped_column(Integer)  # NULL = "and all further"
    discount_percent: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)

    rule: Mapped[ContractDiscountRule] = relationship(back_populates="tiers")


class ContractMoq(Base):
    __tablename__ = "contract_moqs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    quantity: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(32))
    applies_per: Mapped[str | None] = mapped_column(String(32))
    product_scope: Mapped[str | None] = mapped_column(String(200))
    sku: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str | None] = mapped_column(String(8))
    source_clause_text: Mapped[str | None] = mapped_column(Text)


class ContractPriceEntry(Base):
    __tablename__ = "contract_price_list"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    product_name: Mapped[str | None] = mapped_column(String(200))
    product_sku: Mapped[str | None] = mapped_column(String(64))
    unit_price: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(8))
    unit: Mapped[str | None] = mapped_column(String(32))
    list_price_reference: Mapped[str | None] = mapped_column(String(200))
    product_specific_discount_text: Mapped[str | None] = mapped_column(Text)
    source_clause_text: Mapped[str | None] = mapped_column(Text)


class ContractTerritory(Base):
    __tablename__ = "contract_territories"
    __table_args__ = (UniqueConstraint("contract_id", "country_code", "kind", name="uq_contract_territory"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    country_code: Mapped[str] = mapped_column(String(2))
    kind: Mapped[str] = mapped_column(String(16))  # allowed | excluded


class ContractEvidence(Base):
    """Where in the document a field came from — the quotes the reader cited."""

    __tablename__ = "contract_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(100))
    page: Mapped[int | None] = mapped_column(Integer)
    quote: Mapped[str] = mapped_column(Text)
    section_hint: Mapped[str | None] = mapped_column(String(200))
    char_start: Mapped[int | None] = mapped_column(Integer)


# --- conversations ---------------------------------------------------------------

class ChatSession(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    partnership_id: Mapped[str] = mapped_column(ForeignKey("partnerships.id"), index=True)
    specialist: Mapped[str | None] = mapped_column(String(32))
    stage: Mapped[str] = mapped_column(String(16), default="gathering")
    slots: Mapped[dict[str, Any]] = mapped_column(JSONCol, default=dict)
    history: Mapped[list[dict[str, Any]]] = mapped_column(JSONCol, default=list)
    # The burn (`UPDATE … WHERE confirm_token = ?`) is the idempotency guarantee; index it.
    confirm_token: Mapped[str | None] = mapped_column(String(64), index=True)
    confirm_hash: Mapped[str | None] = mapped_column(String(64))
    confirm_expires_at: Mapped[str | None] = mapped_column(String(32))
    confirmed_at: Mapped[str | None] = mapped_column(String(32))
    po_id: Mapped[str | None] = mapped_column(ForeignKey("purchase_orders.id", ondelete="SET NULL"))
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))


# --- purchase orders ---------------------------------------------------------------

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
    submitted_at: Mapped[str | None] = mapped_column(String(32))
    submit_cycle_version: Mapped[int] = mapped_column(Integer, default=0)
    prepaid_amount: Mapped[float] = mapped_column(Float, default=0.0)
    balance_paid: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))


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
    created_at: Mapped[str] = mapped_column(String(32))


class PoEvent(Base):
    __tablename__ = "po_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    po_id: Mapped[str] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict[str, Any]] = mapped_column(JSONCol, default=dict)
    created_at: Mapped[str] = mapped_column(String(32))


# --- brand knowledge base -------------------------------------------------------------
# A brand's own material (decks, catalogues, brand books) kept as a document row plus its
# text per page, and distilled into typed, self-contained fragments. A person approves a
# fragment before any prompt may quote it — the same gate a contract term goes through.

class BrandDocument(Base):
    __tablename__ = "brand_documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    title: Mapped[str | None] = mapped_column(String(200))
    file_name: Mapped[str] = mapped_column(String(255))
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    media_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer)
    pages_processed: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), default="uploaded")  # uploaded | distilled | failed
    distilled_at: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))


class BrandDocumentPage(Base):
    """The text layer of one page — what the reader saw, and what a fragment cites."""

    __tablename__ = "brand_document_pages"
    __table_args__ = (UniqueConstraint("document_id", "page_number", name="uq_document_page"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("brand_documents.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)


class BrandDocumentSection(Base):
    """Pass 1 of the distillation: what kinds of content the document holds, and where."""

    __tablename__ = "brand_document_sections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("brand_documents.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    type: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    page_numbers: Mapped[list[int]] = mapped_column(JSONCol, default=list)


class KnowledgeFragment(Base):
    __tablename__ = "knowledge_fragments"
    __table_args__ = (Index("ix_knowledge_fragments_brand_type", "brand_id", "type"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    # NULL when a person typed the fragment in by hand.
    document_id: Mapped[str | None] = mapped_column(ForeignKey("brand_documents.id", ondelete="SET NULL"), index=True)
    # Set when the fragment is about a catalog product — the join a PO conversation uses.
    product_id: Mapped[str | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), index=True)
    type: Mapped[str] = mapped_column(String(64))  # product | brand_story | proof | audience | … (open slug)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    product_name: Mapped[str | None] = mapped_column(String(200))
    source_pages: Mapped[list[int]] = mapped_column(JSONCol, default=list)
    tags: Mapped[list[str]] = mapped_column(JSONCol, default=list)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | approved | archived
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))


# --- sales reports -------------------------------------------------------------------
# A distributor's sell-out / inventory report: one row per file, each run of a reader
# over it kept in `report_extractions` (how the numbers were located), and the numbers
# themselves as facts at native granularity. Facts are draft until a person confirms the
# report; a later report for the same period supersedes the earlier facts, never doubles them.

class SalesReport(Base):
    __tablename__ = "sales_reports"
    __table_args__ = (Index("ix_sales_reports_partnership_period", "partnership_id", "period_start"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    partnership_id: Mapped[str] = mapped_column(ForeignKey("partnerships.id"))
    title: Mapped[str | None] = mapped_column(String(200))
    file_name: Mapped[str] = mapped_column(String(255))
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    media_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    reporting_period: Mapped[str | None] = mapped_column(String(10))  # YYYY-MM | YYYY-Qn | YYYY
    period_start: Mapped[str | None] = mapped_column(String(10))
    period_end: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | confirmed
    confirmed_at: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))


class ReportExtraction(Base):
    """One run of a reader over the report — provenance for every fact it produced."""

    __tablename__ = "report_extractions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("sales_reports.id", ondelete="CASCADE"), index=True)
    method: Mapped[str] = mapped_column(String(32))  # tidy_table | ai_table_extractor | manual
    model: Mapped[str | None] = mapped_column(String(100))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONCol, default=dict)  # the column mapping / tables located
    skipped: Mapped[list[str]] = mapped_column(JSONCol, default=list)
    extracted_at: Mapped[str] = mapped_column(String(32))


class ReportFact(Base):
    __tablename__ = "report_facts"
    __table_args__ = (
        Index("ix_report_facts_report_status", "report_id", "status"),
        Index("ix_report_facts_sku_period", "sku", "period_start"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("sales_reports.id", ondelete="CASCADE"), index=True)
    extraction_id: Mapped[str | None] = mapped_column(ForeignKey("report_extractions.id", ondelete="SET NULL"))
    metric_key: Mapped[str] = mapped_column(String(64))  # revenue | units_sold | stock_on_hand | … (canonical)
    fact_type: Mapped[str] = mapped_column(String(32))  # sales | inventory | marketing | narrative | …
    channel: Mapped[str | None] = mapped_column(String(100))
    sku: Mapped[str | None] = mapped_column(String(64))
    product_id: Mapped[str | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), index=True)
    entity: Mapped[str | None] = mapped_column(String(200))
    period_start: Mapped[str | None] = mapped_column(String(10))
    period_end: Mapped[str | None] = mapped_column(String(10))
    numeric_value: Mapped[float | None] = mapped_column(Float)
    text_value: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(32))
    currency: Mapped[str | None] = mapped_column(String(8))
    source_sheet: Mapped[str | None] = mapped_column(String(100))
    source_row: Mapped[int | None] = mapped_column(Integer)
    source_col: Mapped[int | None] = mapped_column(Integer)
    source_header: Mapped[str | None] = mapped_column(String(200))  # the column header verbatim
    ai_derived: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | confirmed | superseded
    created_at: Mapped[str] = mapped_column(String(32))


# --- long-term memory ----------------------------------------------------------------
# What the agent should keep in mind about a partnership across conversations — a
# standing preference, an instruction, a fact learned in chat. Read into the PO agent's
# context on every turn; context only, it can never relax a rule.

class Memory(Base):
    __tablename__ = "memories"
    __table_args__ = (Index("ix_memories_partnership_active", "partnership_id", "is_active"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)  # insertion order = recall order
    partnership_id: Mapped[str] = mapped_column(ForeignKey("partnerships.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # preference | fact | instruction | event
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="user")  # user | chat | system
    session_id: Mapped[str | None] = mapped_column(ForeignKey("sessions.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_recalled_at: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))
