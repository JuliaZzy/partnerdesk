"""Contract terms → PO rules: the link the first version never had.

`legacy/contract_ai` extracts a `ContractExtractionDraft` (MOQ, price list, discount
tiers, territory) from the agreement. Those are exactly the inputs the PO code-check
enforces. This maps the extraction into `CommercialRule`s and product MOQs so the next
order the distributor places is checked against the contract automatically.

Mapping (docs/porting/02-rule-engine.md):
  minimum_order_quantities[per_order, amount]  → min_order_value (block)
  minimum_order_quantities[per product, units] → products.moq_units
  excluded_countries                            → territory_restriction (block)
  commercial_discount_rules (highest tier)      → discount_approval at that % (approval_required)

Every rule is named "… (from contract)" so an approver can see where it came from.
Ambiguous fields are reported, not guessed.

CLI:  python -m partnerdesk.contracts extraction.json --brand <brand_id> [--partnership <id>]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any

from .db import Database
from .models import CommercialRule


@dataclass
class ContractRules:
    rules: list[CommercialRule] = field(default_factory=list)
    product_moq_units: dict[str, int] = field(default_factory=dict)  # sku → units
    skipped: list[str] = field(default_factory=list)


def _num(v: Any) -> float | None:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(n) else n


def rules_from_extraction(extraction: dict[str, Any], *, brand_id: str, partnership_id: str | None = None, contract_label: str = "contract") -> ContractRules:
    out = ContractRules()
    scope: dict[str, Any] = (
        {"partner_scope": "specific", "partner_ids": [partnership_id]} if partnership_id else {"partner_scope": "all"}
    )

    def rule(rule_type: str, name: str, config: dict[str, Any], severity: str) -> CommercialRule:
        return CommercialRule(id=f"rule_{uuid.uuid4().hex[:10]}", name=f"{name} (from {contract_label})", rule_type=rule_type,
                              rule_config=config, severity=severity, **scope)

    for moq in extraction.get("minimum_order_quantities") or []:
        if not isinstance(moq, dict):
            continue
        scope_txt = str(moq.get("scope") or moq.get("applies_to") or "").lower()
        sku = moq.get("sku") or moq.get("product_sku")
        qty = _num(moq.get("quantity") or moq.get("value") or moq.get("amount"))
        unit = str(moq.get("unit") or "").lower()
        if qty is None or qty <= 0:
            out.skipped.append(f"MOQ without a usable quantity: {json.dumps(moq, ensure_ascii=False)[:120]}")
            continue
        if sku and unit in ("", "unit", "units", "pcs", "pieces"):
            out.product_moq_units[str(sku)] = int(qty)
        elif "order" in scope_txt and (unit in ("usd", "eur", "cny", "$", "amount", "value") or moq.get("currency")):
            out.rules.append(rule("min_order_value", f"Minimum order value {qty:g}", {"min": qty}, "block"))
        else:
            out.skipped.append(f"MOQ scope ambiguous (not a per-order amount nor a per-SKU unit count): {json.dumps(moq, ensure_ascii=False)[:120]}")

    excluded = [str(c).strip().upper() for c in (extraction.get("excluded_countries") or []) if str(c).strip()]
    if excluded:
        out.rules.append(rule("territory_restriction", f"Excluded territories {', '.join(excluded)}", {"territories": excluded}, "block"))

    best_pct: float | None = None
    for d in extraction.get("commercial_discount_rules") or []:
        if not isinstance(d, dict):
            continue
        for band in d.get("bands") or d.get("tiers") or [d]:
            pct = _num((band or {}).get("discount_pct") or (band or {}).get("percent") or (band or {}).get("discount_percent"))
            if pct is not None and pct > 0:
                best_pct = pct if best_pct is None else max(best_pct, pct)
    if best_pct is not None:
        # A discount beyond what the contract grants is not the distributor's to take.
        out.rules.append(rule("discount_approval", f"Discount above contract maximum {best_pct:g}% needs approval", {"threshold_pct": best_pct + 0.01}, "approval_required"))

    return out


def apply_to_db(db: Database, brand_id: str, result: ContractRules) -> None:
    for r in result.rules:
        db.put_document("rule", r.id, {**r.model_dump(), "brand_id": brand_id})
    if result.product_moq_units:
        for p in db.products(brand_id):
            if p.sku in result.product_moq_units:
                db.put_document("product", p.id, {**p.model_dump(), "brand_id": brand_id, "moq_units": result.product_moq_units[p.sku]})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Map a contract extraction into PO rules.")
    ap.add_argument("extraction", help="JSON file: the `extraction` object from legacy contract_ai analyze_contract")
    ap.add_argument("--brand", required=True)
    ap.add_argument("--partnership")
    ap.add_argument("--apply", action="store_true", help="write the rules + MOQs into the database")
    args = ap.parse_args(argv)
    with open(args.extraction, encoding="utf-8") as f:
        data = json.load(f)
    extraction = data.get("extraction", data)
    result = rules_from_extraction(extraction, brand_id=args.brand, partnership_id=args.partnership)
    if args.apply:
        apply_to_db(Database(), args.brand, result)
    print(json.dumps({
        "rules": [r.model_dump() for r in result.rules], "product_moq_units": result.product_moq_units, "skipped": result.skipped,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
