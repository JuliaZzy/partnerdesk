"""Commercial rules evaluator — deterministic, pure, no I/O.

Rules are brand-owned and scoped by partner (all / specific / region) and product
(all / specific / role / tag). Nine rule types; see docs/porting/02-rule-engine.md.

Only `block` + triggered stops an order. `warn` / `approval_required` + triggered are
advisories: the order still submits, the user is told it will need the brand's approval.
Every live, partner-matching rule produces a result (pass or triggered) — the whole list
is persisted as the submission snapshot so an approver sees what was evaluated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..models import CommercialRule, Product


@dataclass
class EvalItem:
    product_id: str
    sku: str
    product_name: str
    quantity: int  # cases
    case_pack: int | None
    case_price: float | None
    line_total: float | None
    product: Product | None = None


@dataclass
class POContext:
    discount_percentage: float = 0.0
    ship_to_country: str | None = None
    partnership_id: str | None = None


@dataclass
class RuleResult:
    rule_id: str
    rule_name: str
    rule_type: str
    severity: str
    status: str  # pass | triggered
    message: str
    affected_items: list[dict[str, Any]] = field(default_factory=list)

    @property
    def triggered(self) -> bool:
        return self.status == "triggered"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id, "rule_name": self.rule_name, "rule_type": self.rule_type,
            "severity": self.severity, "status": self.status, "message": self.message,
            "affected_items": self.affected_items,
        }


# --- lifecycle ---------------------------------------------------------------

def rule_lifecycle(rule: CommercialRule, today: str) -> str:
    """paused | scheduled | expired | live. ISO dates compare lexically."""
    if not rule.is_active:
        return "paused"
    if rule.effective_from and today < rule.effective_from:
        return "scheduled"
    if rule.effective_until and today > rule.effective_until:
        return "expired"
    return "live"


def is_rule_live(rule: CommercialRule, today: str) -> bool:
    return rule_lifecycle(rule, today) == "live"


# --- helpers -----------------------------------------------------------------

def _cfg(config: dict[str, Any], snake: str, camel: str, default: Any) -> Any:
    """Config keys were written both ways by the first version's UI; accept both."""
    v = config.get(snake)
    if v is None:
        v = config.get(camel)
    return default if v is None else v


def _line_value(i: EvalItem) -> float:
    if i.line_total is not None and i.line_total > 0:
        return i.line_total
    price = i.case_price if i.case_price is not None else (i.product.case_price if i.product and i.product.case_price else 0.0)
    return (i.quantity or 0) * (price or 0.0)


def _line_cases(i: EvalItem) -> int:
    return i.quantity or 0


def _line_units(i: EvalItem) -> int:
    cp = i.case_pack or (i.product.case_pack if i.product else None) or 1
    return (i.quantity or 0) * (cp if cp > 0 else 1)


def _role(i: EvalItem) -> str:
    return (i.product.commercial_role or "").lower() if i.product else ""


def _applies_to_partner(rule: CommercialRule, po: POContext) -> bool:
    if rule.partner_scope == "specific":
        return bool(po.partnership_id) and po.partnership_id in rule.partner_ids
    if rule.partner_scope == "region":
        country = (po.ship_to_country or "").upper()
        return country in {r.upper() for r in rule.partner_regions}
    return True


def _scoped(rule: CommercialRule, items: list[EvalItem]) -> list[EvalItem]:
    if rule.product_scope == "specific":
        return [i for i in items if i.product_id in rule.product_ids]
    if rule.product_scope == "role":
        roles = {r.lower() for r in rule.product_roles}
        return [i for i in items if _role(i) in roles]
    if rule.product_scope == "tag":
        tags = {t.lower() for t in rule.product_tags}
        return [i for i in items if i.product and tags & {t.lower() for t in i.product.policy_tags}]
    return items


def _affected(items: list[EvalItem], qty_fn=_line_cases, with_value: bool = True) -> list[dict[str, Any]]:
    out = []
    for i in items:
        row: dict[str, Any] = {"product_name": i.product_name, "sku": i.sku, "quantity": qty_fn(i)}
        if with_value:
            row["value"] = round(_line_value(i), 2)
        out.append(row)
    return out


# --- evaluator ---------------------------------------------------------------

def evaluate_rules(
    po: POContext,
    line_items: list[EvalItem],
    rules: list[CommercialRule],
    today: str | None = None,
) -> list[RuleResult]:
    today = today or date.today().isoformat()
    results: list[RuleResult] = []

    for rule in rules:
        if not is_rule_live(rule, today) or not _applies_to_partner(rule, po):
            continue
        items = _scoped(rule, line_items)
        cfg = rule.rule_config or {}

        def res(triggered: bool, msg: str, affected: list[dict[str, Any]] | None = None, *, rule: CommercialRule = rule) -> RuleResult:
            return RuleResult(
                rule_id=rule.id, rule_name=rule.name, rule_type=rule.rule_type, severity=rule.severity,
                status="triggered" if triggered else "pass", message=msg,
                affected_items=affected or [],
            )

        t = rule.rule_type
        if t == "gift_pct_cap":
            max_pct = float(_cfg(cfg, "max_pct", "maxPct", 10))
            total = sum(_line_value(i) for i in items)
            gifts = [i for i in items if _role(i) == "gift"]
            gift_value = sum(_line_value(i) for i in gifts)
            pct = (gift_value / total * 100) if total > 0 else 0.0
            trig = gift_value > 0 and pct > max_pct
            over = gift_value - total * max_pct / 100
            results.append(res(
                trig,
                f"Gift items are {pct:.1f}% of order value but the limit is {max_pct:g}%. Remove or reduce the gift items by ${over:.2f} or more."
                if trig else f"Gift item ratio is within limit ({pct:.1f}% of {max_pct:g}% max).",
                _affected(gifts) if trig else None,
            ))
        elif t == "sample_unit_cap":
            max_units = int(_cfg(cfg, "max_units", "maxUnits", 24))
            samples = [i for i in items if _role(i) == "sample"]
            n = sum(_line_cases(i) for i in samples)
            trig = n > max_units
            results.append(res(
                trig,
                f"{n} sample units ordered (max {max_units}). Reduce sample quantities by at least {n - max_units}."
                if trig else f"Sample unit count is within limit ({n} of {max_units} max).",
                _affected(samples) if trig else None,
            ))
        elif t == "territory_restriction":
            restricted = [str(x).upper() for x in (cfg.get("territories") or [])]
            country = (po.ship_to_country or "").upper()
            if not restricted or not country:
                results.append(res(False, "No territory restrictions apply."))
            else:
                trig = country in restricted
                results.append(res(
                    trig,
                    f"Shipping to {country} is not permitted. Change the ship-to destination to proceed."
                    if trig else f"Destination {country} is not in the restricted territories list.",
                ))
        elif t == "discount_approval":
            threshold = float(_cfg(cfg, "threshold_pct", "thresholdPct", 15))
            pct = po.discount_percentage or 0.0
            trig = pct >= threshold
            results.append(res(
                trig,
                f"Your discount of {pct:.1f}% exceeds the {threshold:g}% threshold. This order requires brand approval before it can be confirmed."
                if trig else f"Discount of {pct:.1f}% is below the {threshold:g}% approval threshold.",
            ))
        elif t == "discontinued_block":
            disc = [i for i in items if i.product and i.product.discontinued]
            trig = len(disc) > 0
            results.append(res(
                trig,
                f"Your order includes {len(disc)} discontinued product(s). Remove them to proceed."
                if trig else "No discontinued products in this order.",
                _affected(disc, with_value=False) if trig else None,
            ))
        elif t == "min_order_value":
            minimum = float(_cfg(cfg, "min", "min", 0))
            total = sum(_line_value(i) for i in items)
            trig = total < minimum
            results.append(res(
                trig,
                f"Order value is ${total:.2f}, which is ${minimum - total:.2f} below the ${minimum:.2f} minimum. Add more items to proceed."
                if trig else f"Order value ${total:.2f} meets the ${minimum:.2f} minimum.",
            ))
        elif t == "unit_cap":
            max_units = int(_cfg(cfg, "max_units", "maxUnits", 24))
            # Legacy rules predate `unit`; the evaluator historically counted cases.
            unit_mode = "unit" if cfg.get("unit") == "unit" else "case"
            fn = _line_units if unit_mode == "unit" else _line_cases
            n = sum(fn(i) for i in items)
            trig = n > max_units
            noun = "units" if unit_mode == "unit" else "cases"
            results.append(res(
                trig,
                f"{n} {noun} ordered for restricted products (max {max_units}). Reduce by at least {n - max_units} {noun}."
                if trig else f"{noun.capitalize()} count is within limit ({n} of {max_units} max).",
                _affected(items, fn) if trig else None,
            ))
        elif t == "value_pct_cap":
            max_pct = float(_cfg(cfg, "max_pct", "maxPct", 10))
            total_po = sum(_line_value(i) for i in line_items)
            scoped_value = sum(_line_value(i) for i in items)
            pct = (scoped_value / total_po * 100) if total_po > 0 else 0.0
            trig = scoped_value > 0 and pct > max_pct
            over = scoped_value - total_po * max_pct / 100
            results.append(res(
                trig,
                f"These items total ${scoped_value:.2f}, which is {pct:.1f}% of PO value (max {max_pct:g}%). Reduce them by ${over:.2f} or more."
                if trig else f"Scoped item ratio is within limit ({pct:.1f}% of {max_pct:g}% max).",
                _affected(items) if trig else None,
            ))
        elif t == "block_all":
            trig = len(items) > 0
            results.append(res(
                trig,
                f"{len(items)} item(s) in this order are not permitted. Remove them to proceed."
                if trig else "No blocked items in this order.",
                _affected(items, with_value=False) if trig else None,
            ))
        # unknown rule types are ignored, as in the first version

    return results


def moq_minimum_cases(moq_units: int | None, case_pack: int | None) -> int | None:
    """Minimum line quantity in CASES from catalog MOQ in units + units per case."""
    if not moq_units or moq_units <= 0 or not case_pack or case_pack <= 0:
        return None
    return math.ceil(moq_units / case_pack)
