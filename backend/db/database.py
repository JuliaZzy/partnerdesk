"""The one object the rest of the code talks to for persistence.

It speaks pydantic models (`backend/schemas/`) and plain dicts outward and SQLAlchemy
rows (`orm.py`) inward — nothing outside this package imports `orm`. Every method is one
transaction; the multi-row writes (an order with its items and rule snapshot, a contract
with its terms and derived rules) commit together or not at all.

Construct it with a PostgreSQL URL, or nothing to use `config.db_url()`.
The schema is migrated to head on construction.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Engine, func, inspect, or_, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, sessionmaker

from ..config import db_url
from ..schemas import (
    AgentSettings,
    BrandDocument,
    CommercialRule,
    Contract,
    ContractChunk,
    ContractDiscountRule,
    ContractDiscountTier,
    ContractEvidence,
    ContractMoq,
    ContractPriceEntry,
    ContractRecord,
    ContractTerms,
    ContractTerritory,
    Memory,
    Partnership,
    Product,
    PurchaseOrder,
    ReportFact,
    SalesReport,
)
from . import orm
from .engine import make_engine, run_migrations


def utcnow() -> datetime:
    """The one clock the tables are stamped from — always UTC-aware, so what goes into a
    `timestamptz` column carries its offset instead of inheriting the server's."""
    return datetime.now(UTC)


def _as_url(target: str | None) -> str:
    return db_url() if target is None else str(target)


def _row_dict(row: Any) -> dict[str, Any]:
    """Column values, skipping ones not loaded. A deferred blob (the uploaded file) stays
    out of every list and detail read that did not ask for it."""
    unloaded = inspect(row).unloaded
    return {c.key: getattr(row, c.key) for c in row.__table__.columns if c.key not in unloaded}


class Database:
    def __init__(self, target: str | None = None, engine: Engine | None = None):
        self.url = _as_url(target) if engine is None else str(engine.url)
        self.engine = engine or make_engine(self.url)
        run_migrations(self.engine)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    def tx(self) -> Session:
        """`with db.tx() as s:` — commits on exit, rolls back on error."""
        return self._sessions.begin()

    # --- upserts (admin, tests) ----------------------------------------------------
    def _upsert(self, s: Session, cls: type, pk: Any, values: dict[str, Any]) -> None:
        ts = utcnow()
        row = s.get(cls, pk)
        cols = {c.key for c in cls.__table__.columns}
        values = {k: v for k, v in values.items() if k in cols}
        if row is None:
            if "created_at" in cols:
                values.setdefault("created_at", ts)
            if "updated_at" in cols:
                values["updated_at"] = ts
            s.add(cls(**values))
        else:
            values.pop("created_at", None)
            if "updated_at" in cols:
                values["updated_at"] = ts
            for k, v in values.items():
                setattr(row, k, v)

    def upsert_brand(self, id: str, name: str, preferred_currency: str = "USD") -> None:
        with self.tx() as s:
            self._upsert(s, orm.Brand, id, {"id": id, "name": name, "preferred_currency": preferred_currency})

    def upsert_distributor(self, id: str, name: str, country: str | None = None) -> None:
        with self.tx() as s:
            self._upsert(s, orm.Distributor, id, {"id": id, "name": name, "country": country})

    def upsert_partnership(self, p: Partnership) -> None:
        """Creates the brand and distributor rows from the names on the model when they
        don't exist yet, so a partnership can be written from one record."""
        with self.tx() as s:
            if s.get(orm.Brand, p.brand_id) is None:
                self._upsert(s, orm.Brand, p.brand_id, {"id": p.brand_id, "name": p.brand_name, "preferred_currency": p.currency})
            if s.get(orm.Distributor, p.distributor_id) is None:
                self._upsert(s, orm.Distributor, p.distributor_id, {"id": p.distributor_id, "name": p.distributor_name, "country": p.ship_to_country})
            self._upsert(s, orm.Partnership, p.id, p.model_dump(exclude={"brand_name", "distributor_name"}))

    def upsert_product(self, brand_id: str, p: Product) -> None:
        self.upsert_products(brand_id, [p])

    def upsert_products(self, brand_id: str, products: list[Product]) -> None:
        """One transaction for a batch of products."""
        with self.tx() as s:
            # Load the existing rows up front so `_upsert`'s get() is an identity-map hit, not a query each.
            s.scalars(select(orm.Product).where(orm.Product.id.in_([p.id for p in products]))).all()
            for p in products:
                self._upsert(s, orm.Product, p.id, {**p.model_dump(), "brand_id": brand_id})

    def upsert_rule(self, brand_id: str, r: CommercialRule, contract_id: str | None = None) -> None:
        with self.tx() as s:
            self._upsert(s, orm.CommercialRule, r.id, {**r.model_dump(), "brand_id": brand_id, "contract_id": contract_id})

    def put_settings(self, brand_id: str, settings: AgentSettings) -> None:
        with self.tx() as s:
            self._upsert(s, orm.BrandAgentSettings, brand_id, {**settings.model_dump(), "brand_id": brand_id})

    # --- reads ---------------------------------------------------------------------------
    def products(self, brand_id: str) -> list[Product]:
        with self.tx() as s:
            rows = s.scalars(select(orm.Product).where(orm.Product.brand_id == brand_id).order_by(orm.Product.id)).all()
            return [Product(**_row_dict(r)) for r in rows]

    def product_names(self, brand_id: str, skus: list[str]) -> dict[str, str]:
        """Native name, else English, for these SKUs on this brand. Unknown SKUs are omitted.

        The catalog is large, so this looks up the given SKUs instead of loading every product.
        """
        wanted = list(dict.fromkeys(sku.strip() for sku in skus if isinstance(sku, str) and sku.strip()))
        if not wanted:
            return {}
        with self.tx() as s:
            found = s.execute(
                select(orm.Product.sku, orm.Product.product_name_native, orm.Product.product_name_en)
                .where(orm.Product.brand_id == brand_id, orm.Product.sku.in_(wanted))
            ).all()
        names: dict[str, str] = {}
        for sku, native, english in found:
            name = (native or "").strip() or (english or "").strip()
            if name:
                names[sku] = name
        return names

    def rules(self, brand_id: str) -> list[CommercialRule]:
        with self.tx() as s:
            rows = s.scalars(select(orm.CommercialRule).where(orm.CommercialRule.brand_id == brand_id).order_by(orm.CommercialRule.id)).all()
            return [CommercialRule(**_row_dict(r)) for r in rows]

    @staticmethod
    def _partnership(row: orm.Partnership) -> Partnership:
        return Partnership(
            id=row.id, brand_id=row.brand_id, distributor_id=row.distributor_id, brand_name=row.brand.name,
            distributor_name=row.distributor.name, ship_to_country=row.ship_to_country, currency=row.currency,
        )

    def partnership(self, id: str) -> Partnership | None:
        with self.tx() as s:
            row = s.get(orm.Partnership, id)
            return self._partnership(row) if row else None

    def partnerships(self) -> list[Partnership]:
        with self.tx() as s:
            return [self._partnership(r) for r in s.scalars(select(orm.Partnership).order_by(orm.Partnership.id)).all()]

    def settings(self, brand_id: str) -> AgentSettings:
        with self.tx() as s:
            row = s.get(orm.BrandAgentSettings, brand_id)
            d = _row_dict(row) if row else {}
            return AgentSettings(**{k: v for k, v in d.items() if v is not None and k != "brand_id"})

    # --- catalog: every brand's products at once -------------------------------------------
    def replace_catalog(self, brands: Iterable[dict[str, Any]], products: Iterable[dict[str, Any]]) -> dict[str, int]:
        """Make the product table exactly `products`, in one transaction.

        Hundreds of thousands of rows, so it goes through COPY into a staging table and one
        upsert rather than an ORM object per row. Only brands some product belongs to are
        written, upserted and never deleted — they carry partnerships, rules and settings.
        A product missing from the new load is deleted, unless an order line points at it:
        an order keeps its product."""
        product_cols = [c.key for c in orm.Product.__table__.columns if c.key not in ("created_at", "updated_at")]
        json_cols = {c.key for c in orm.Product.__table__.columns if isinstance(c.type, JSONB)}
        cols = ", ".join(product_cols)
        ts = utcnow()
        raw = self.engine.raw_connection()
        try:
            with raw.cursor() as cur:
                # CREATE TABLE AS copies the column types but none of the NOT NULLs, which the
                # timestamps would trip over: those are stamped on the way out of staging.
                cur.execute("CREATE TEMP TABLE stage_brands ON COMMIT DROP AS SELECT id, name, preferred_currency FROM brands WITH NO DATA")
                with cur.copy("COPY stage_brands (id, name, preferred_currency) FROM STDIN") as cp:
                    for b in brands:
                        cp.write_row((b["id"], b["name"], b.get("preferred_currency") or "USD"))
                cur.execute(f"CREATE TEMP TABLE stage_products ON COMMIT DROP AS SELECT {cols} FROM products WITH NO DATA")
                with cur.copy(f"COPY stage_products ({cols}) FROM STDIN") as cp:
                    for p in products:
                        cp.write_row([json.dumps(p.get(c) or []) if c in json_cols else p.get(c) for c in product_cols])

                cur.execute(
                    "INSERT INTO brands (id, name, preferred_currency, created_at, updated_at)"
                    " SELECT id, name, preferred_currency, %(ts)s, %(ts)s FROM stage_brands b"
                    " WHERE EXISTS (SELECT 1 FROM stage_products p WHERE p.brand_id = b.id)"
                    " ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, updated_at = EXCLUDED.updated_at",
                    {"ts": ts},
                )
                n_brands = cur.rowcount
                updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in product_cols if c != "id")
                cur.execute(
                    f"INSERT INTO products ({cols}, created_at, updated_at) SELECT {cols}, %(ts)s, %(ts)s FROM stage_products"
                    f" ON CONFLICT (id) DO UPDATE SET {updates}, updated_at = EXCLUDED.updated_at",
                    {"ts": ts},
                )
                n_products = cur.rowcount
                cur.execute(
                    "DELETE FROM products p WHERE NOT EXISTS (SELECT 1 FROM stage_products s WHERE s.id = p.id)"
                    " AND NOT EXISTS (SELECT 1 FROM purchase_order_items i WHERE i.product_id = p.id)"
                )
                n_removed = cur.rowcount
            raw.commit()
        except BaseException:
            raw.rollback()
            raise
        finally:
            raw.close()
        return {"brands": n_brands, "products": n_products, "removed": n_removed}

    @staticmethod
    def _catalog_where(query: str | None, brand: str | None, category: str | None, status: str | None) -> list[Any]:
        def like(text: str) -> str:
            return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

        where: list[Any] = []
        if q := (query or "").strip():
            where.append(or_(*(col.ilike(like(q), escape="\\") for col in (
                orm.Product.sku, orm.Product.product_name_en, orm.Product.product_name_native,
            ))))
        if b := (brand or "").strip():
            where.append(orm.Brand.name.ilike(like(b), escape="\\"))
        if category:
            where.append(orm.Product.category == category)
        if status:
            where.append(orm.Product.product_status == status)
        return where

    def search_catalog(
        self, query: str | None = None, *, brand: str | None = None, category: str | None = None,
        status: str | None = None, limit: int = 100, offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """One page of the catalog across all brands, with the total the filters match.
        `query` is a substring of the SKU or either name; `brand` a substring of the brand name."""
        where = self._catalog_where(query, brand, category, status)
        joined = select(orm.Product, orm.Brand.name).join(orm.Brand, orm.Brand.id == orm.Product.brand_id).where(*where)
        with self.tx() as s:
            total = s.scalar(select(func.count()).select_from(joined.subquery())) or 0
            rows = s.execute(joined.order_by(orm.Brand.name, orm.Product.sku).limit(limit).offset(offset)).all()
            return [
                {**Product(**_row_dict(p)).model_dump(), "brand_id": p.brand_id, "brand_name": name} for p, name in rows
            ], total

    def catalog_stats(
        self, query: str | None = None, *, brand: str | None = None, category: str | None = None, status: str | None = None,
    ) -> dict[str, Any]:
        """Counts for the same filters as `search_catalog`: products per status, brands, categories."""
        where = self._catalog_where(query, brand, category, status)
        base = select(orm.Product.product_status, orm.Product.brand_id, orm.Product.category).join(
            orm.Brand, orm.Brand.id == orm.Product.brand_id,
        ).where(*where).subquery()
        with self.tx() as s:
            by_status = dict(s.execute(select(base.c.product_status, func.count()).group_by(base.c.product_status)).all())
            brands, categories = s.execute(
                select(func.count(base.c.brand_id.distinct()), func.count(base.c.category.distinct()))
            ).one()
        return {"by_status": by_status, "brands": brands, "categories": categories}

    def catalog_categories(self) -> list[str]:
        with self.tx() as s:
            return list(s.scalars(select(orm.Product.category).where(orm.Product.category.is_not(None)).distinct().order_by(orm.Product.category)))

    # --- contracts -----------------------------------------------------------------------
    def save_contract(self, rec: ContractRecord) -> str:
        """One transaction: supersede the partnership's active contract, write the contract +
        raw extraction + normalized terms, then the derived rules and product MOQs. Returns
        the contract id."""
        c, ts = rec.contract, utcnow()
        with self.tx() as s:
            for old in s.scalars(select(orm.Contract).where(orm.Contract.partnership_id == c.partnership_id, orm.Contract.status == "active", orm.Contract.id != c.id)):
                old.status, old.updated_at = "superseded", ts
            self._upsert(s, orm.Contract, c.id, {**c.model_dump(exclude={"created_at"}), "status": "active"})
            s.flush()  # children below reference it; no relationship tells the unit of work the order
            s.add(orm.ContractExtraction(
                id=uuid.uuid4().hex, contract_id=c.id, model=rec.model, confidence_score=rec.confidence_score,
                extraction_notes=rec.extraction_notes, raw=rec.extraction_raw, skipped=list(rec.skipped), extracted_at=ts,
            ))
            # Terms are replaced wholesale by the newest extraction of this contract.
            for cls in (
                orm.ContractDiscountRule, orm.ContractMoq, orm.ContractPriceEntry, orm.ContractTerritory,
                orm.ContractEvidence, orm.ContractChunk,
            ):
                for row in s.scalars(select(cls).where(cls.contract_id == c.id)):
                    s.delete(row)
            s.flush()
            for ch in rec.chunks:
                s.add(orm.ContractChunk(contract_id=c.id, **ch.model_dump()))
            for i, dr in enumerate(rec.terms.discount_rules):
                s.add(orm.ContractDiscountRule(
                    contract_id=c.id, position=i, title=dr.title, section_reference=dr.section_reference, applies_per=dr.applies_per,
                    basis=dr.basis, source_clause_text=dr.source_clause_text,
                    tiers=[orm.ContractDiscountTier(position=j, **t.model_dump(exclude={"position"})) for j, t in enumerate(dr.tiers)],
                ))
            for m in rec.terms.moqs:
                s.add(orm.ContractMoq(contract_id=c.id, **m.model_dump()))
            for pe in rec.terms.price_list:
                s.add(orm.ContractPriceEntry(contract_id=c.id, **pe.model_dump()))
            for t in rec.terms.territories:
                s.add(orm.ContractTerritory(contract_id=c.id, **t.model_dump()))
            for e in rec.terms.evidence:
                s.add(orm.ContractEvidence(contract_id=c.id, **e.model_dump()))
            # Rules previously derived from this contract are replaced, not accumulated.
            for row in s.scalars(select(orm.CommercialRule).where(orm.CommercialRule.contract_id == c.id)):
                s.delete(row)
            s.flush()
            for r in rec.derived_rules:
                self._upsert(s, orm.CommercialRule, r.id, {**r.model_dump(), "brand_id": c.brand_id, "contract_id": c.id})
            if rec.product_moq_units:
                for p in s.scalars(select(orm.Product).where(orm.Product.brand_id == c.brand_id)):
                    if p.sku in rec.product_moq_units:
                        p.moq_units, p.updated_at = rec.product_moq_units[p.sku], ts
        return c.id

    def contract(self, id: str) -> Contract | None:
        with self.tx() as s:
            row = s.get(orm.Contract, id)
            return Contract(**_row_dict(row)) if row else None

    def contracts(self, partnership_id: str) -> list[Contract]:
        """Every agreement on file for the partnership — the active one first, then superseded."""
        with self.tx() as s:
            rows = s.scalars(
                select(orm.Contract).where(orm.Contract.partnership_id == partnership_id)
                .order_by(orm.Contract.status, orm.Contract.created_at.desc())
            ).all()
            return [Contract(**_row_dict(r)) for r in rows]

    def derived_rules(self, contract_id: str) -> list[CommercialRule]:
        """The commercial rules this contract's terms were mapped into."""
        with self.tx() as s:
            rows = s.scalars(select(orm.CommercialRule).where(orm.CommercialRule.contract_id == contract_id).order_by(orm.CommercialRule.id)).all()
            return [CommercialRule(**_row_dict(r)) for r in rows]

    def active_contract(self, partnership_id: str) -> Contract | None:
        with self.tx() as s:
            row = s.scalars(
                select(orm.Contract).where(orm.Contract.partnership_id == partnership_id, orm.Contract.status == "active")
                .order_by(orm.Contract.created_at.desc())
            ).first()
            return Contract(**_row_dict(row)) if row else None

    def contract_discount_rules(self, contract_id: str) -> list[ContractDiscountRule]:
        with self.tx() as s:
            rows = s.scalars(select(orm.ContractDiscountRule).where(orm.ContractDiscountRule.contract_id == contract_id).order_by(orm.ContractDiscountRule.position)).all()
            return [
                ContractDiscountRule(**{**_row_dict(r), "tiers": [ContractDiscountTier(**_row_dict(t)) for t in r.tiers]})
                for r in rows
            ]

    def contract_terms(self, contract_id: str) -> ContractTerms:
        with self.tx() as s:
            def rows(cls: type) -> list[dict[str, Any]]:
                return [_row_dict(r) for r in s.scalars(select(cls).where(cls.contract_id == contract_id).order_by(cls.id))]
            return ContractTerms(
                discount_rules=self.contract_discount_rules(contract_id),
                moqs=[ContractMoq(**d) for d in rows(orm.ContractMoq)],
                price_list=[ContractPriceEntry(**d) for d in rows(orm.ContractPriceEntry)],
                territories=[ContractTerritory(**d) for d in rows(orm.ContractTerritory)],
                evidence=[ContractEvidence(**d) for d in rows(orm.ContractEvidence)],
            )

    def contract_chunks(self, contract_id: str) -> list[ContractChunk]:
        with self.tx() as s:
            rows = s.scalars(
                select(orm.ContractChunk)
                .where(orm.ContractChunk.contract_id == contract_id)
                .order_by(orm.ContractChunk.page_start, orm.ContractChunk.sibling_order, orm.ContractChunk.id),
            ).all()
            return [ContractChunk(**_row_dict(r)) for r in rows]

    def contract_extractions(self, contract_id: str) -> list[dict[str, Any]]:
        with self.tx() as s:
            rows = s.scalars(select(orm.ContractExtraction).where(orm.ContractExtraction.contract_id == contract_id).order_by(orm.ContractExtraction.extracted_at)).all()
            return [_row_dict(r) for r in rows]

    # --- sessions ---------------------------------------------------------------------
    def create_session(self, id: str, partnership_id: str) -> dict[str, Any]:
        ts = utcnow()
        with self.tx() as s:
            s.add(orm.ChatSession(
                id=id, partnership_id=partnership_id, title=None, tags=[], slots={}, history=[],
                created_at=ts, updated_at=ts,
            ))
        return self.session(id)  # type: ignore[return-value]

    def session(self, id: str) -> dict[str, Any] | None:
        with self.tx() as s:
            row = s.get(orm.ChatSession, id)
            return _row_dict(row) if row else None

    def list_sessions(self, partnership_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """A partnership's chats, most recently touched first (the sidebar's chat list)."""
        with self.tx() as s:
            rows = s.scalars(
                select(orm.ChatSession).where(orm.ChatSession.partnership_id == partnership_id)
                .order_by(orm.ChatSession.updated_at.desc(), orm.ChatSession.created_at.desc()).limit(limit)
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
        """Remove the chat. A memory or query log that pointed here keeps its row and loses the link.

        The order it placed stays too: `po_id` points from the chat at the order, not the other way.
        """
        with self.tx() as s:
            row = s.get(orm.ChatSession, id)
            if row is None:
                raise KeyError(f"session {id} not found")
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

    # --- purchase orders --------------------------------------------------------------
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


    # --- brand documents -------------------------------------------------------------
    def save_document(self, doc: BrandDocument, pages: list[tuple[int, str]], *, file_bytes: bytes | None = None) -> str:
        """The uploaded file as a row plus its text per page.
        `file_bytes` is the original upload, kept so a later index can render page images."""
        values = doc.model_dump(exclude={"created_at", "updated_at"})
        if file_bytes is not None:
            values["file_bytes"] = file_bytes
        with self.tx() as s:
            self._upsert(s, orm.BrandDocument, doc.id, values)
            s.flush()
            for row in s.scalars(select(orm.BrandDocumentPage).where(orm.BrandDocumentPage.document_id == doc.id)):
                s.delete(row)
            s.flush()
            s.add_all(orm.BrandDocumentPage(document_id=doc.id, page_number=n, text=t) for n, t in pages)
        return doc.id

    def documents(self, brand_id: str) -> list[BrandDocument]:
        with self.tx() as s:
            rows = s.scalars(select(orm.BrandDocument).where(orm.BrandDocument.brand_id == brand_id).order_by(orm.BrandDocument.created_at.desc())).all()
            return [BrandDocument(**_row_dict(r)) for r in rows]

    def document(self, id: str) -> BrandDocument | None:
        with self.tx() as s:
            row = s.get(orm.BrandDocument, id)
            return BrandDocument(**_row_dict(row)) if row else None

    def document_bytes(self, document_id: str) -> bytes | None:
        with self.tx() as s:
            row = s.get(orm.BrandDocument, document_id)
            return row.file_bytes if row else None

    def document_pages(self, document_id: str) -> list[tuple[int, str]]:
        with self.tx() as s:
            rows = s.scalars(select(orm.BrandDocumentPage).where(orm.BrandDocumentPage.document_id == document_id).order_by(orm.BrandDocumentPage.page_number)).all()
            return [(r.page_number, r.text) for r in rows]

    def delete_document(self, id: str) -> None:
        """Pages go with the document (ON DELETE CASCADE)."""
        with self.tx() as s:
            row = s.get(orm.BrandDocument, id)
            if row is not None:
                s.delete(row)

    # --- sales reports ---------------------------------------------------------------
    def save_report(
        self, report: SalesReport, *, method: str, model: str | None, raw: dict[str, Any], skipped: list[str],
        facts: list[ReportFact],
    ) -> str:
        """One transaction: the report, the extraction run that read it, and its draft facts."""
        ts, xid = utcnow(), uuid.uuid4().hex
        with self.tx() as s:
            self._upsert(s, orm.SalesReport, report.id, report.model_dump(exclude={"created_at", "updated_at"}))
            s.flush()
            s.add(orm.ReportExtraction(id=xid, report_id=report.id, method=method, model=model, raw=raw, skipped=list(skipped), extracted_at=ts))
            s.add_all(
                orm.ReportFact(**{**f.model_dump(exclude={"id", "created_at", "report_id", "extraction_id"}), "report_id": report.id, "extraction_id": xid, "created_at": ts})
                for f in facts
            )
        return report.id

    def reports(self, partnership_id: str) -> list[SalesReport]:
        with self.tx() as s:
            rows = s.scalars(
                select(orm.SalesReport).where(orm.SalesReport.partnership_id == partnership_id)
                .order_by(orm.SalesReport.period_start.desc(), orm.SalesReport.created_at.desc())
            ).all()
            return [SalesReport(**_row_dict(r)) for r in rows]

    def report(self, id: str) -> SalesReport | None:
        with self.tx() as s:
            row = s.get(orm.SalesReport, id)
            return SalesReport(**_row_dict(row)) if row else None

    def report_facts(self, report_id: str, status: str | None = None) -> list[ReportFact]:
        with self.tx() as s:
            q = select(orm.ReportFact).where(orm.ReportFact.report_id == report_id)
            if status:
                q = q.where(orm.ReportFact.status == status)
            return [ReportFact(**_row_dict(r)) for r in s.scalars(q.order_by(orm.ReportFact.id)).all()]

    def report_extractions(self, report_id: str) -> list[dict[str, Any]]:
        with self.tx() as s:
            rows = s.scalars(select(orm.ReportExtraction).where(orm.ReportExtraction.report_id == report_id).order_by(orm.ReportExtraction.extracted_at)).all()
            return [_row_dict(r) for r in rows]

    def confirm_report(self, report_id: str) -> dict[str, int]:
        """The gate for a report's numbers. Drafts become confirmed; a confirmed fact from
        another report of the same partnership measuring the same thing for the same period
        is superseded — a re-sent month replaces, it never doubles. Repeat calls are no-ops."""
        ts = utcnow()
        with self.tx() as s:
            rep = s.get(orm.SalesReport, report_id)
            if rep is None:
                raise KeyError(f"report {report_id} not found")
            drafts = s.scalars(select(orm.ReportFact).where(orm.ReportFact.report_id == report_id, orm.ReportFact.status == "draft")).all()
            keys = {ReportFact(**_row_dict(f)).identity() for f in drafts}
            superseded = 0
            if keys:
                others = s.scalars(
                    select(orm.ReportFact).join(orm.SalesReport, orm.ReportFact.report_id == orm.SalesReport.id).where(
                        orm.SalesReport.partnership_id == rep.partnership_id, orm.ReportFact.report_id != report_id,
                        orm.ReportFact.status == "confirmed",
                    )
                ).all()
                for f in others:
                    if ReportFact(**_row_dict(f)).identity() in keys:
                        f.status, superseded = "superseded", superseded + 1
            for f in drafts:
                f.status = "confirmed"
            rep.status, rep.confirmed_at, rep.updated_at = "confirmed", rep.confirmed_at or ts, ts
            return {"confirmed": len(drafts), "superseded": superseded}

    def confirmed_facts(self, partnership_id: str, fact_type: str | None = None) -> list[ReportFact]:
        """The live numbers for a partnership — confirmed and not superseded, across reports."""
        with self.tx() as s:
            q = (
                select(orm.ReportFact).join(orm.SalesReport, orm.ReportFact.report_id == orm.SalesReport.id)
                .where(orm.SalesReport.partnership_id == partnership_id, orm.ReportFact.status == "confirmed")
            )
            if fact_type:
                q = q.where(orm.ReportFact.fact_type == fact_type)
            return [ReportFact(**_row_dict(r)) for r in s.scalars(q.order_by(orm.ReportFact.period_start, orm.ReportFact.id)).all()]

    def delete_report(self, id: str) -> None:
        with self.tx() as s:
            row = s.get(orm.SalesReport, id)
            if row is not None:
                s.delete(row)

    # --- long-term memory ------------------------------------------------------------
    def add_memory(self, m: Memory) -> int:
        ts = utcnow()
        with self.tx() as s:
            row = orm.Memory(**m.model_dump(exclude={"id", "created_at", "updated_at"}), created_at=ts, updated_at=ts)
            s.add(row)
            s.flush()
            return row.id

    def memories(self, partnership_id: str, *, active_only: bool = True) -> list[Memory]:
        with self.tx() as s:
            q = select(orm.Memory).where(orm.Memory.partnership_id == partnership_id)
            if active_only:
                q = q.where(orm.Memory.is_active.is_(True))
            return [Memory(**_row_dict(r)) for r in s.scalars(q.order_by(orm.Memory.id.desc())).all()]

    def set_memory_active(self, id: int, active: bool) -> None:
        with self.tx() as s:
            row = s.get(orm.Memory, id)
            if row is None:
                raise KeyError(f"memory {id} not found")
            row.is_active, row.updated_at = active, utcnow()

    def mark_memories_recalled(self, ids: list[int]) -> None:
        if not ids:
            return
        ts = utcnow()
        with self.tx() as s:
            s.execute(update(orm.Memory).where(orm.Memory.id.in_(ids)).values(last_recalled_at=ts))

    def delete_memory(self, id: int) -> None:
        with self.tx() as s:
            row = s.get(orm.Memory, id)
            if row is not None:
                s.delete(row)

    # --- text2sql -----------------------------------------------------------------------
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


_READER_ROLE = "text2sql_reader"


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


__all__ = ["Database", "utcnow"]
