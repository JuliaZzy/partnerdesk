"""Contract → database → purchase orders.

`legacy/contract_ai` reads an agreement into a `ContractExtractionDraft` (discount tiers,
MOQ, price list, territory, evidence). This module keeps ALL of it: the raw extraction is
stored verbatim, the terms are normalized into their own tables, and from those terms it
derives what the PO gate needs:

  minimum_order_quantities[per_order, money]  → min_order_value rule (block)
  minimum_order_quantities[per SKU, units]    → products.moq_units
  excluded_countries                          → territory_restriction rule (block)
  commercial_discount_rules (max tier)        → discount_approval rule (approval_required)
  commercial_discount_rules (tier schedule)   → the discount PRE-FILLED on the next order
                                                (`contract_discount_for_next_order`)

Every derived rule carries `contract_id`, so an approver can follow it back to the clause.
Ambiguous fields are reported in `skipped`, never guessed.

CLI (manual ops entry point):
  python -m partnerdesk.contracts extraction.json --brand <brand_id> --partnership <id> [--apply]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from .db import Database
from .models import (
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
)

MONEY_UNITS = ("usd", "eur", "cny", "gbp", "sek", "$", "€", "amount", "value")
UNIT_UNITS = ("", "unit", "units", "pcs", "pieces", "piece")


def _num(v: Any) -> float | None:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(n) or math.isinf(n) else n


def _int(v: Any) -> int | None:
    n = _num(v)
    return int(n) if n is not None else None


def _str(v: Any) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s or None


# --- extraction → normalized terms -------------------------------------------------

def _discount_rules(extraction: dict[str, Any], skipped: list[str]) -> list[ContractDiscountRule]:
    out: list[ContractDiscountRule] = []
    for i, d in enumerate(extraction.get("commercial_discount_rules") or []):
        if not isinstance(d, dict):
            continue
        tiers: list[ContractDiscountTier] = []
        for j, band in enumerate(d.get("tiers") or d.get("bands") or []):
            if not isinstance(band, dict):
                continue
            pct = _num(band.get("discount_percent") if band.get("discount_percent") is not None else band.get("discount_pct", band.get("percent")))
            if pct is None or pct < 0:
                skipped.append(f"discount tier without a usable percent: {json.dumps(band, ensure_ascii=False)[:120]}")
                continue
            tiers.append(ContractDiscountTier(
                position=j, from_container=_int(band.get("from_container")), to_container=_int(band.get("to_container")),
                discount_percent=pct, notes=_str(band.get("notes")),
            ))
        if not tiers:
            skipped.append(f"discount rule without tiers: {_str(d.get('title')) or _str(d.get('source_clause_text')) or '(untitled)'}")
            continue
        out.append(ContractDiscountRule(
            position=i, title=_str(d.get("title")), section_reference=_str(d.get("section_reference")),
            applies_per=_str(d.get("applies_per")), basis=_str(d.get("basis")), source_clause_text=_str(d.get("source_clause_text")),
            tiers=tiers,
        ))
    return out


def _moqs(extraction: dict[str, Any]) -> list[ContractMoq]:
    out: list[ContractMoq] = []
    for m in extraction.get("minimum_order_quantities") or []:
        if not isinstance(m, dict):
            continue
        out.append(ContractMoq(
            quantity=_num(m.get("quantity") if m.get("quantity") is not None else m.get("value", m.get("amount"))),
            unit=_str(m.get("unit")), applies_per=_str(m.get("applies_per") or m.get("scope")),
            product_scope=_str(m.get("product_scope")), sku=_str(m.get("sku") or m.get("product_sku")),
            currency=_str(m.get("currency")), source_clause_text=_str(m.get("source_clause_text")),
        ))
    return out


def _price_list(extraction: dict[str, Any]) -> list[ContractPriceEntry]:
    return [ContractPriceEntry(**e) for e in (extraction.get("product_price_list") or []) if isinstance(e, dict)]


def _territories(extraction: dict[str, Any]) -> list[ContractTerritory]:
    out: list[ContractTerritory] = []
    seen: set[tuple[str, str]] = set()
    for kind, key in (("allowed", "allowed_countries"), ("excluded", "excluded_countries")):
        for c in extraction.get(key) or []:
            code = str(c).strip().upper()
            if code and (code, kind) not in seen:
                seen.add((code, kind))
                out.append(ContractTerritory(country_code=code, kind=kind))  # type: ignore[arg-type]
    return out


def _evidence(extraction: dict[str, Any]) -> list[ContractEvidence]:
    out: list[ContractEvidence] = []
    fe = extraction.get("field_evidence") or {}
    if not isinstance(fe, dict):
        return out
    for field_name, entries in fe.items():
        for e in entries or []:
            if isinstance(e, dict) and _str(e.get("quote")):
                out.append(ContractEvidence(
                    field=str(field_name), quote=str(e["quote"]).strip(), page=_int(e.get("page")),
                    section_hint=_str(e.get("section_hint")), char_start=_int(e.get("char_start")),
                ))
    return out


def terms_from_extraction(extraction: dict[str, Any]) -> tuple[ContractTerms, list[str]]:
    skipped: list[str] = []
    terms = ContractTerms(
        discount_rules=_discount_rules(extraction, skipped), moqs=_moqs(extraction), price_list=_price_list(extraction),
        territories=_territories(extraction), evidence=_evidence(extraction),
    )
    return terms, skipped


# --- normalized terms → PO gate ------------------------------------------------------

def derive_rules(terms: ContractTerms, *, partnership_id: str | None, contract_label: str = "contract") -> tuple[list[CommercialRule], dict[str, int], list[str]]:
    """Rules + per-SKU MOQs the PO code-check enforces. Returns (rules, sku→moq_units, skipped)."""
    rules: list[CommercialRule] = []
    moq_units: dict[str, int] = {}
    skipped: list[str] = []
    scope: dict[str, Any] = {"partner_scope": "specific", "partner_ids": [partnership_id]} if partnership_id else {"partner_scope": "all"}

    def rule(rule_type: str, name: str, config: dict[str, Any], severity: str) -> CommercialRule:
        return CommercialRule(id=f"rule_{uuid.uuid4().hex[:10]}", name=f"{name} (from {contract_label})", rule_type=rule_type,
                              rule_config=config, severity=severity, **scope)

    for m in terms.moqs:
        unit = (m.unit or "").lower()
        if m.quantity is None or m.quantity <= 0:
            skipped.append(f"MOQ without a usable quantity: {m.model_dump(exclude_none=True)}")
        elif m.sku and unit in UNIT_UNITS:
            moq_units[m.sku] = int(m.quantity)
        elif "order" in (m.applies_per or "").lower() and (unit in MONEY_UNITS or m.currency):
            rules.append(rule("min_order_value", f"Minimum order value {m.quantity:g}", {"min": m.quantity}, "block"))
        else:
            skipped.append(f"MOQ scope ambiguous (not a per-order amount nor a per-SKU unit count): {m.model_dump(exclude_none=True)}")

    excluded = [t.country_code for t in terms.territories if t.kind == "excluded"]
    if excluded:
        rules.append(rule("territory_restriction", f"Excluded territories {', '.join(excluded)}", {"territories": excluded}, "block"))

    best = [r.max_percent for r in terms.discount_rules if r.max_percent is not None]
    if best:
        # A discount beyond what the contract grants is not the distributor's to take.
        best_pct = max(best)
        rules.append(rule("discount_approval", f"Discount above contract maximum {best_pct:g}% needs approval", {"threshold_pct": best_pct + 0.01}, "approval_required"))
    return rules, moq_units, skipped


def record_from_extraction(
    extraction: dict[str, Any], *, brand_id: str, partnership_id: str, contract_id: str | None = None,
    title: str | None = None, file_name: str | None = None, file_sha256: str | None = None, model: str | None = None,
) -> ContractRecord:
    """Pure: the full record one ingestion writes. Nothing here touches the database."""
    terms, skipped = terms_from_extraction(extraction)
    cid = contract_id or f"ct_{uuid.uuid4().hex[:12]}"
    rules, moq_units, more_skipped = derive_rules(terms, partnership_id=partnership_id, contract_label=title or "contract")
    contract = Contract(
        id=cid, brand_id=brand_id, partnership_id=partnership_id, title=title, file_name=file_name, file_sha256=file_sha256,
        contract_type=_str(extraction.get("contract_type")),
        effective_from=_str(extraction.get("effective_date")) or _str(extraction.get("term_start_date")),
        term_start_date=_str(extraction.get("term_start_date")), term_end_date=_str(extraction.get("term_end_date")),
        auto_renewal=extraction.get("auto_renewal") if isinstance(extraction.get("auto_renewal"), bool) else None,
        exclusivity_type=_str(extraction.get("exclusivity_type")), territory_text=_str(extraction.get("territory_text")),
        annual_sales_target=extraction.get("annual_sales_target") if isinstance(extraction.get("annual_sales_target"), dict) else None,
        annual_sales_target_currency=_str(extraction.get("annual_sales_target_currency")),
    )
    return ContractRecord(
        contract=contract, extraction_raw=extraction, model=model, confidence_score=_num(extraction.get("confidence_score")),
        extraction_notes=_str(extraction.get("extraction_notes")), skipped=[*skipped, *more_skipped], terms=terms,
        derived_rules=rules, product_moq_units=moq_units,
    )


def ingest_extraction(db: Database, extraction: dict[str, Any], *, brand_id: str, partnership_id: str, **meta: Any) -> ContractRecord:
    """Build the record and write it in one transaction. Returns what was written."""
    rec = record_from_extraction(extraction, brand_id=brand_id, partnership_id=partnership_id, **meta)
    db.save_contract(rec)
    return rec


# --- the discount the next order gets -------------------------------------------------

@dataclass(frozen=True)
class ContractDiscount:
    percent: float
    container_index: int
    period_start: str
    rule_title: str | None
    section_reference: str | None

    @property
    def note(self) -> str:
        ordinal = {1: "1st", 2: "2nd", 3: "3rd"}.get(self.container_index, f"{self.container_index}th")
        ref = f" §{self.section_reference}" if self.section_reference else ""
        what = f"{self.percent:g}% off" if self.percent > 0 else "list price"
        return f"{what} — {ordinal} container of the contract period from {self.period_start}{ref}"


def _period_start(contract: Contract, applies_per: str | None, today: date) -> str:
    """When the current counting period began. Contract years roll over on the anniversary
    of the effective date; calendar years on 1 January; anything else counts from the start."""
    anchor = contract.effective_from or contract.term_start_date or contract.created_at or today.isoformat()
    anchor_date = date.fromisoformat(anchor[:10])
    per = (applies_per or "").lower()
    if "calendar" in per:
        return date(today.year, 1, 1).isoformat()
    if "contract" in per or "year" in per:
        start = _anniversary(anchor_date, today.year)
        return (start if start <= today else _anniversary(anchor_date, today.year - 1)).isoformat()
    return anchor_date.isoformat()


def _anniversary(anchor: date, year: int) -> date:
    try:
        return anchor.replace(year=year)
    except ValueError:  # 29 February
        return anchor.replace(year=year, day=28)


def contract_discount_for_next_order(db: Database, partnership_id: str, today: date | None = None) -> ContractDiscount | None:
    """The tier the partnership's active contract grants the order about to be placed, or
    None when the contract doesn't settle it (no contract, no sequence-based tiers).

    ASSUMPTION: one submitted purchase order = one container. That is how this brand's
    distributors order (a PO is a container load); if a partnership ships several POs per
    container the count overstates the tier, so the approval threshold (max tier) still
    caps what can be taken without a person looking.
    """
    today = today or date.today()
    contract = db.active_contract(partnership_id)
    if not contract:
        return None
    for rule in db.contract_discount_rules(contract.id):
        if rule.basis and "container" not in rule.basis.lower() and "sequence" not in rule.basis.lower():
            continue  # volume/rebate schedules aren't a per-order rate this code can pick
        if not rule.tiers or all(t.from_container is None and t.to_container is None for t in rule.tiers):
            continue
        since = _period_start(contract, rule.applies_per, today)
        n = db.count_submitted_pos(partnership_id, since=since) + 1
        tier = next((t for t in rule.tiers if t.covers(n)), None)
        if tier is None or tier.discount_percent is None:
            continue
        return ContractDiscount(percent=float(tier.discount_percent), container_index=n, period_start=since,
                                rule_title=rule.title, section_reference=rule.section_reference)
    return None


# --- CLI ----------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Store a contract extraction and derive PO rules from it.")
    ap.add_argument("extraction", help="JSON file: the `extraction` object from legacy contract_ai analyze_contract")
    ap.add_argument("--brand", required=True)
    ap.add_argument("--partnership", required=True)
    ap.add_argument("--title")
    ap.add_argument("--apply", action="store_true", help="write the contract, its terms, rules and MOQs into the database")
    args = ap.parse_args(argv)
    with open(args.extraction, encoding="utf-8") as f:
        data = json.load(f)
    extraction = data.get("extraction", data)
    meta = {"title": args.title, "file_name": args.extraction}
    rec = (
        ingest_extraction(Database(), extraction, brand_id=args.brand, partnership_id=args.partnership, **meta)
        if args.apply else record_from_extraction(extraction, brand_id=args.brand, partnership_id=args.partnership, **meta)
    )
    print(json.dumps({
        "contract_id": rec.contract.id, "written": args.apply,
        "terms": rec.terms.model_dump(exclude_none=True), "rules": [r.model_dump() for r in rec.derived_rules],
        "product_moq_units": rec.product_moq_units, "skipped": rec.skipped,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
