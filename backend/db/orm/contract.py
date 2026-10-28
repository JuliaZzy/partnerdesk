"""Tables for `schemas/contract.py`.

One `contracts` row per agreement; every extraction of it is kept verbatim in
`contract_extractions`, and the terms the agent acts on are normalized into the child
tables below. Rules derived from a contract point back at it via `commercial_rules.contract_id`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, JSONCol, TimestampCol


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
    created_at: Mapped[datetime] = mapped_column(TimestampCol)
    updated_at: Mapped[datetime] = mapped_column(TimestampCol)

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
    extracted_at: Mapped[datetime] = mapped_column(TimestampCol)


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


class ContractChunk(Base):
    """Heading tree for one agreement — deterministic from `contract.chunks.split_chunks`."""

    __tablename__ = "contract_chunks"
    __table_args__ = (UniqueConstraint("contract_id", "chunk_id", name="uq_contract_chunk"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    chunk_id: Mapped[str] = mapped_column(String(16))
    parent_chunk_id: Mapped[str | None] = mapped_column(String(16))
    number: Mapped[str] = mapped_column(String(64), default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    path: Mapped[str] = mapped_column(String(500), default="")
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    sibling_order: Mapped[int] = mapped_column(Integer, default=0)
