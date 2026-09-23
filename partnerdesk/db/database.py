"""The one object the rest of the code talks to for persistence.

It speaks pydantic models (`partnerdesk/models.py`) and plain dicts outward and SQLAlchemy
rows (`orm.py`) inward — nothing outside this package imports `orm`. Every method is one
transaction; the multi-row writes (an order with its items and rule snapshot, a contract
with its terms and derived rules) commit together or not at all.

Construct it with a SQLAlchemy URL, a file path (→ SQLite) or nothing (`config.db_url()`).
The schema is migrated to head on construction.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from ..config import FIXTURES_DIR, db_url
from ..models import (
    AgentSettings,
    BrandDocument,
    CommercialRule,
    Contract,
    ContractDiscountRule,
    ContractDiscountTier,
    ContractEvidence,
    ContractMoq,
    ContractPriceEntry,
    ContractRecord,
    ContractTerms,
    ContractTerritory,
    DocumentSection,
    KnowledgeFragment,
    Memory,
    Partnership,
    Product,
    PurchaseOrder,
    ReportFact,
    SalesReport,
)
from . import orm
from .engine import make_engine, run_migrations


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _as_url(target: str | Path | None) -> str:
    if target is None:
        return db_url()
    s = str(target)
    return s if "://" in s else f"sqlite:///{Path(s).as_posix()}"


def _row_dict(row: Any) -> dict[str, Any]:
    return {c.key: getattr(row, c.key) for c in row.__table__.columns}


class Database:
    def __init__(self, target: str | Path | None = None, engine: Engine | None = None):
        self.url = _as_url(target) if engine is None else str(engine.url)
        self.engine = engine or make_engine(self.url)
        run_migrations(self.engine)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    def tx(self) -> Session:
        """`with db.tx() as s:` — commits on exit, rolls back on error."""
        return self._sessions.begin()

    # --- upserts (seeding, admin, tests) -------------------------------------------
    def _upsert(self, s: Session, cls: type, pk: Any, values: dict[str, Any]) -> None:
        ts = now_iso()
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
        don't exist yet, so a partnership can be seeded from one record."""
        with self.tx() as s:
            if s.get(orm.Brand, p.brand_id) is None:
                self._upsert(s, orm.Brand, p.brand_id, {"id": p.brand_id, "name": p.brand_name, "preferred_currency": p.currency})
            if s.get(orm.Distributor, p.distributor_id) is None:
                self._upsert(s, orm.Distributor, p.distributor_id, {"id": p.distributor_id, "name": p.distributor_name, "country": p.ship_to_country})
            self._upsert(s, orm.Partnership, p.id, p.model_dump(exclude={"brand_name", "distributor_name"}))

    def upsert_product(self, brand_id: str, p: Product) -> None:
        with self.tx() as s:
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

    def seed_from_fixtures(self, fixtures_dir: Path = FIXTURES_DIR) -> None:
        from .seed import seed_from_fixtures  # lazy: seed imports contracts, which imports this package

        seed_from_fixtures(self, fixtures_dir)

    # --- contracts -----------------------------------------------------------------------
    def save_contract(self, rec: ContractRecord) -> str:
        """One transaction: supersede the partnership's active contract, write the contract +
        raw extraction + normalized terms, then the derived rules and product MOQs. Returns
        the contract id."""
        c, ts = rec.contract, now_iso()
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
            for cls in (orm.ContractDiscountRule, orm.ContractMoq, orm.ContractPriceEntry, orm.ContractTerritory, orm.ContractEvidence):
                for row in s.scalars(select(cls).where(cls.contract_id == c.id)):
                    s.delete(row)
            s.flush()
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

    def contract_extractions(self, contract_id: str) -> list[dict[str, Any]]:
        with self.tx() as s:
            rows = s.scalars(select(orm.ContractExtraction).where(orm.ContractExtraction.contract_id == contract_id).order_by(orm.ContractExtraction.extracted_at)).all()
            return [_row_dict(r) for r in rows]

    # --- sessions ---------------------------------------------------------------------
    def create_session(self, id: str, partnership_id: str) -> dict[str, Any]:
        ts = now_iso()
        with self.tx() as s:
            s.add(orm.ChatSession(id=id, partnership_id=partnership_id, slots={}, history=[], created_at=ts, updated_at=ts))
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
            row.updated_at = now_iso()

    def burn_confirm_token(self, token: str) -> str | None:
        """Atomically consume a confirm token. Returns the session id, or None when the
        token was already used — two clicks landing together see one UPDATE match and one
        match nothing. Done BEFORE the order is created, never after."""
        ts = now_iso()
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
        ts = now_iso()
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

    def count_submitted_pos(self, partnership_id: str, since: str | None = None) -> int:
        """Orders this partnership has placed (submitted_at set), optionally from a date."""
        with self.tx() as s:
            q = select(func.count()).select_from(orm.PurchaseOrder).where(
                orm.PurchaseOrder.partnership_id == partnership_id, orm.PurchaseOrder.submitted_at.is_not(None),
            )
            if since:
                q = q.where(orm.PurchaseOrder.submitted_at >= since)
            return int(s.scalar(q) or 0)

    def update_po(self, id: str, **fields: Any) -> None:
        with self.tx() as s:
            row = s.get(orm.PurchaseOrder, id)
            if row is None:
                raise KeyError(f"purchase order {id} not found")
            for k, v in fields.items():
                setattr(row, k, v)
            row.updated_at = now_iso()

    def add_po_event(self, po_id: str, kind: str, data: dict[str, Any]) -> None:
        with self.tx() as s:
            s.add(orm.PoEvent(po_id=po_id, kind=kind, data=data, created_at=now_iso()))

    def po_events(self, po_id: str) -> list[dict[str, Any]]:
        with self.tx() as s:
            rows = s.scalars(select(orm.PoEvent).where(orm.PoEvent.po_id == po_id).order_by(orm.PoEvent.id)).all()
            return [{"kind": r.kind, "data": r.data, "created_at": r.created_at} for r in rows]


    # --- brand knowledge base --------------------------------------------------------
    def save_document(self, doc: BrandDocument, pages: list[tuple[int, str]]) -> str:
        """The uploaded file as a row plus its text per page; nothing distilled yet."""
        with self.tx() as s:
            self._upsert(s, orm.BrandDocument, doc.id, doc.model_dump(exclude={"created_at", "updated_at"}))
            s.flush()
            for row in s.scalars(select(orm.BrandDocumentPage).where(orm.BrandDocumentPage.document_id == doc.id)):
                s.delete(row)
            s.flush()
            s.add_all(orm.BrandDocumentPage(document_id=doc.id, page_number=n, text=t) for n, t in pages)
        return doc.id

    def save_distillation(
        self, document_id: str, *, summary: str | None, model: str | None, pages_processed: int | None,
        sections: list[DocumentSection], fragments: list[KnowledgeFragment], status: str = "distilled",
    ) -> None:
        """One transaction: the document's scan result, its sections, and its fragments.
        Fragments a person already approved (or archived) survive a re-run; drafts are replaced."""
        ts = now_iso()
        with self.tx() as s:
            row = s.get(orm.BrandDocument, document_id)
            if row is None:
                raise KeyError(f"document {document_id} not found")
            row.summary, row.model, row.pages_processed, row.status, row.distilled_at, row.updated_at = summary, model, pages_processed, status, ts, ts
            for sec in s.scalars(select(orm.BrandDocumentSection).where(orm.BrandDocumentSection.document_id == document_id)):
                s.delete(sec)
            for fr in s.scalars(select(orm.KnowledgeFragment).where(orm.KnowledgeFragment.document_id == document_id, orm.KnowledgeFragment.status == "draft")):
                s.delete(fr)
            s.flush()
            for i, sec in enumerate(sections):
                s.add(orm.BrandDocumentSection(document_id=document_id, position=i, **sec.model_dump()))
            for f in fragments:
                self._upsert(s, orm.KnowledgeFragment, f.id, {**f.model_dump(exclude={"created_at", "updated_at"}), "document_id": document_id})

    def documents(self, brand_id: str) -> list[BrandDocument]:
        with self.tx() as s:
            rows = s.scalars(select(orm.BrandDocument).where(orm.BrandDocument.brand_id == brand_id).order_by(orm.BrandDocument.created_at.desc())).all()
            return [BrandDocument(**_row_dict(r)) for r in rows]

    def document(self, id: str) -> BrandDocument | None:
        with self.tx() as s:
            row = s.get(orm.BrandDocument, id)
            return BrandDocument(**_row_dict(row)) if row else None

    def document_pages(self, document_id: str) -> list[tuple[int, str]]:
        with self.tx() as s:
            rows = s.scalars(select(orm.BrandDocumentPage).where(orm.BrandDocumentPage.document_id == document_id).order_by(orm.BrandDocumentPage.page_number)).all()
            return [(r.page_number, r.text) for r in rows]

    def document_sections(self, document_id: str) -> list[DocumentSection]:
        with self.tx() as s:
            rows = s.scalars(select(orm.BrandDocumentSection).where(orm.BrandDocumentSection.document_id == document_id).order_by(orm.BrandDocumentSection.position)).all()
            return [DocumentSection(**_row_dict(r)) for r in rows]

    def delete_document(self, id: str) -> None:
        """Pages and sections go with it; fragments stay, with document_id set to NULL."""
        with self.tx() as s:
            row = s.get(orm.BrandDocument, id)
            if row is not None:
                s.delete(row)

    def fragments(
        self, brand_id: str, *, status: str | None = None, type: str | None = None, document_id: str | None = None,
    ) -> list[KnowledgeFragment]:
        with self.tx() as s:
            q = select(orm.KnowledgeFragment).where(orm.KnowledgeFragment.brand_id == brand_id)
            if status:
                q = q.where(orm.KnowledgeFragment.status == status)
            if type:
                q = q.where(orm.KnowledgeFragment.type == type)
            if document_id:
                q = q.where(orm.KnowledgeFragment.document_id == document_id)
            rows = s.scalars(q.order_by(orm.KnowledgeFragment.type, orm.KnowledgeFragment.title)).all()
            return [KnowledgeFragment(**_row_dict(r)) for r in rows]

    def upsert_fragment(self, f: KnowledgeFragment) -> str:
        with self.tx() as s:
            self._upsert(s, orm.KnowledgeFragment, f.id, f.model_dump(exclude={"created_at", "updated_at"}))
        return f.id

    def set_fragment_status(self, id: str, status: str) -> None:
        with self.tx() as s:
            row = s.get(orm.KnowledgeFragment, id)
            if row is None:
                raise KeyError(f"fragment {id} not found")
            row.status, row.updated_at = status, now_iso()

    def delete_fragment(self, id: str) -> None:
        with self.tx() as s:
            row = s.get(orm.KnowledgeFragment, id)
            if row is not None:
                s.delete(row)

    # --- sales reports ---------------------------------------------------------------
    def save_report(
        self, report: SalesReport, *, method: str, model: str | None, raw: dict[str, Any], skipped: list[str],
        facts: list[ReportFact],
    ) -> str:
        """One transaction: the report, the extraction run that read it, and its draft facts."""
        ts, xid = now_iso(), uuid.uuid4().hex
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
        ts = now_iso()
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
        ts = now_iso()
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
            row.is_active, row.updated_at = active, now_iso()

    def mark_memories_recalled(self, ids: list[int]) -> None:
        if not ids:
            return
        ts = now_iso()
        with self.tx() as s:
            s.execute(update(orm.Memory).where(orm.Memory.id.in_(ids)).values(last_recalled_at=ts))

    def delete_memory(self, id: int) -> None:
        with self.tx() as s:
            row = s.get(orm.Memory, id)
            if row is not None:
                s.delete(row)


__all__ = ["Database", "now_iso"]
