"""The PO agent's deterministic "code check" gate.

The LLM only extracts the conversation into slots (a product REFERENCE + cases per line);
THIS resolves references to catalog products and decides whether the order is enough to
build a valid, rule-compliant PO — in code, not by the model. It:
  1. resolves each line reference against the catalog (search + disambiguation: an exact
     SKU, one fuzzy match, several matches → ask, or none → ask),
  2. prices each line and enforces MOQ (units → cases),
  3. runs the same commercial-rules engine the submit path relies on, so a passing check
     means submit won't be rejected.

`ready` is the only thing standing between an LLM turn and a real purchase order.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..catalog import Catalog
from ..models import CommercialRule, Product
from .rules import EvalItem, POContext, RuleResult, evaluate_rules, moq_minimum_cases
from .slots import PoSlots

# Structural sanity bound on a single line — NOT a commercial limit (those are the brand's
# rules). Without it nothing caps quantity: 999,999,999 cases priced and passed every
# check, `ready` with zero gaps, on its way to a ~$100bn purchase order.
MAX_CASES_PER_LINE = 100_000

Resolver = Callable[[list[str]], dict[str, list[str]]]


@dataclass
class ResolvedLine:
    reference: str
    sku: str | None
    cases: int
    product_id: str | None = None
    product_name: str = ""
    case_pack: int | None = None
    case_price: float | None = None
    line_total: float | None = None
    moq_cases: int | None = None
    cases_per_layer: int | None = None
    partial_layer: bool = False
    cases_to_full_layer: int | None = None
    unresolved: bool = False
    ambiguous: bool = False
    candidates: list[dict[str, str]] = field(default_factory=list)
    below_moq: bool = False
    missing_price: bool = False

    @property
    def resolved(self) -> bool:
        return self.product_id is not None


@dataclass
class Gap:
    kind: str
    message: str
    reference: str | None = None
    candidates: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "message": self.message, "reference": self.reference, "candidates": self.candidates}


@dataclass
class CheckResult:
    lines: list[ResolvedLine]
    subtotal: float
    gaps: list[Gap]
    blocking: list[RuleResult]
    advisory: list[RuleResult]
    rule_results: list[RuleResult]
    ready: bool

    @property
    def gap_messages(self) -> list[str]:
        return [g.message for g in self.gaps] + [r.message for r in self.blocking]


# --- search ------------------------------------------------------------------

def _haystack(p: Product) -> str:
    return " ".join(
        x for x in (
            p.product_name_en, p.native_name, p.sku, p.category, p.subcategory, p.variant, p.form,
            p.unit_size, p.net_content, p.description_en,
        ) if x
    ).lower()


_SPLIT = re.compile(r"[\s,/、，。]+")


def search_catalog(reference: str, catalog: Catalog) -> tuple[list[Product], bool]:
    """Exact SKU wins; then an exact product name; otherwise every product tying for the
    best token-overlap score (a true tie is ambiguity). `exact` marks the two cases where
    the customer named a product outright rather than gestured at one."""
    q = (reference or "").strip().lower()
    if not q:
        return [], False
    if q in catalog.by_sku:
        return [catalog.by_sku[q]], True

    # Naming a product EXACTLY is an answer, not a hint — it is how the disambiguation
    # buttons reply. Without this, "Radiance Serum" scores the same as every product it is
    # a prefix of, and picking the shorter name re-asks the question forever.
    exact_name = [
        p for p in catalog.products
        if (p.product_name_en or "").strip().lower() == q or (p.native_name or "").strip().lower() == q
    ]
    if len(exact_name) == 1:
        return exact_name, True

    # Whole-reference substring PLUS latin word overlap. The substring is what makes
    # non-latin references work ("鱼油" ⊂ "深海鱼油omega-3"). Whatever this misses resolves
    # semantically in resolver.py — don't add heuristics here.
    tokens = [t for t in _SPLIT.split(q) if len(t) >= 2]
    scored: list[tuple[int, Product]] = []
    for p in catalog.products:
        hay = _haystack(p)
        score = 0
        if len(q) >= 2 and q in hay:
            score += 2
        score += sum(1 for t in tokens if t in hay)
        if score > 0:
            scored.append((score, p))
    if not scored:
        return [], False
    top = max(s for s, _ in scored)
    return [p for s, p in scored if s == top][:6], False


# --- resolution + pricing ----------------------------------------------------

def _priced(p: Product, cases: int, reference: str, slot_sku: str | None) -> ResolvedLine:
    moq_cases = moq_minimum_cases(p.moq_units, p.case_pack)
    cpl = p.cases_per_layer if p.cases_per_layer and p.cases_per_layer > 0 else None
    partial = cpl is not None and cases > 0 and cases % cpl != 0
    to_full = (-(-cases // cpl) * cpl - cases) if partial and cpl else None
    return ResolvedLine(
        reference=reference, sku=p.sku or slot_sku, cases=cases, product_id=p.id,
        product_name=p.display_name, case_pack=p.case_pack, case_price=p.case_price,
        line_total=round(p.case_price * cases, 2) if p.case_price is not None else None,
        moq_cases=moq_cases, cases_per_layer=cpl, partial_layer=partial, cases_to_full_layer=to_full,
        below_moq=moq_cases is not None and cases > 0 and cases < moq_cases,
        missing_price=p.case_price is None,
    )


def _candidates(ps: list[Product]) -> list[dict[str, str]]:
    return [{"sku": p.sku, "name": p.display_name} for p in ps]


def _resolve_lines(slots: PoSlots, catalog: Catalog) -> list[ResolvedLine]:
    out: list[ResolvedLine] = []
    for li in slots.line_items:
        cases = max(0, int(li.cases or 0))
        matches, exact = search_catalog(li.reference, catalog)
        # A `sku` on the slot is EITHER one this code resolved on an earlier turn (trustworthy)
        # OR one the model volunteered despite being told not to guess. Two things outrank it:
        #  1. the customer naming a product outright — measured: asked for "radiance serum"
        #     against Radiance Serum ($50) and Radiance Serum Plus ($60), gpt-4.1 supplied
        #     SR-200 and llama3.2:3b supplied SR-201; same words, different price;
        #  2. a genuine tie in the search — a guess must not settle a question the catalog
        #     itself says is open.
        known = catalog.by_sku.get(li.sku.lower()) if li.sku else None
        if exact:
            out.append(_priced(matches[0], cases, li.reference, li.sku))
        elif known and len(matches) <= 1:
            out.append(_priced(known, cases, li.reference, li.sku))
        elif len(matches) == 1:
            out.append(_priced(matches[0], cases, li.reference, li.sku))
        else:
            ambiguous = len(matches) > 1
            out.append(ResolvedLine(
                reference=li.reference, sku=li.sku, cases=cases, product_name=li.reference,
                unresolved=not ambiguous, ambiguous=ambiguous,
                candidates=_candidates(matches) if ambiguous else [],
            ))
    return out


def _apply_llm_resolution(lines: list[ResolvedLine], catalog: Catalog, resolve: Resolver) -> list[ResolvedLine]:
    """Re-resolve what the search missed using the model's candidate SKUs. Only catalog-valid
    SKUs survive — a hallucinated id is dropped, not ordered. 1 → priced, several → ambiguous
    (agent asks), 0 → stays unresolved. A model failure leaves everything as it was."""
    refs = list({l.reference for l in lines if l.unresolved and l.reference})
    if not refs:
        return lines
    try:
        mapping = resolve(refs)
    except Exception:
        return lines
    out: list[ResolvedLine] = []
    for l in lines:
        if not l.unresolved:
            out.append(l)
            continue
        seen: set[str] = set()
        matched: list[Product] = []
        for sku in mapping.get(l.reference, []) or []:
            p = catalog.by_sku.get(str(sku).lower())
            if p and p.id not in seen:
                seen.add(p.id)
                matched.append(p)
        if len(matched) == 1:
            out.append(_priced(matched[0], l.cases, l.reference, matched[0].sku))
        elif len(matched) > 1:
            l.unresolved, l.ambiguous, l.candidates = False, True, _candidates(matched)
            out.append(l)
        else:
            out.append(l)
    return out


# --- gaps ----------------------------------------------------------------------

def _collect_gaps(lines: list[ResolvedLine], subtotal: float, slots: PoSlots) -> list[Gap]:
    gaps: list[Gap] = []
    if not lines:
        gaps.append(Gap("no_items", "What would you like to order? Tell me the products and how many cases of each."))
    for l in lines:
        if l.cases > MAX_CASES_PER_LINE:
            gaps.append(Gap("implausible_quantity", f"{l.product_name}: {l.cases:,} cases looks like a mistake — please confirm the quantity you meant.", l.reference))
            continue  # one question per line; quantity first
        if l.ambiguous:
            names = ", ".join(c["name"] for c in l.candidates)
            gaps.append(Gap("ambiguous_product", f'Several products match "{l.reference}" — which did you mean: {names}?', l.reference, l.candidates))
        elif l.unresolved:
            gaps.append(Gap("unresolved_reference", f'I couldn\'t find "{l.reference}" in your catalog — which product did you mean?', l.reference))
        elif l.below_moq:
            gaps.append(Gap("below_moq", f"{l.product_name} needs at least {l.moq_cases} cases (minimum order quantity) — you have {l.cases}.", l.reference))
        elif l.missing_price:
            gaps.append(Gap("missing_price", f"{l.product_name} has no price on file — please confirm the case price or pick another product.", l.reference))
    if lines and all(l.resolved for l in lines) and subtotal <= 0:
        gaps.append(Gap("zero_total", "The order total is $0 — please add quantities or products with a price."))
    if slots.discount is None:
        gaps.append(Gap("discount_unaddressed", "Is this order at list price, or is there an agreed discount to put on it?"))
    return gaps


def discount_pct(slots: PoSlots, subtotal: float) -> float:
    d = slots.discount
    if not d or d.kind == "none" or subtotal <= 0:
        return 0.0
    if d.kind == "percent":
        return max(0.0, float(d.value or 0))
    return max(0.0, float(d.value or 0) / subtotal * 100)


# --- entry point ---------------------------------------------------------------

def run_code_check(
    *,
    slots: PoSlots,
    catalog: Catalog,
    rules: list[CommercialRule],
    partnership_id: str,
    ship_to_country: str | None,
    resolve: Resolver | None = None,
    today: str | None = None,
) -> CheckResult:
    lines = _resolve_lines(slots, catalog)
    if resolve:
        lines = _apply_llm_resolution(lines, catalog, resolve)
    subtotal = round(sum(l.line_total or 0 for l in lines), 2)
    gaps = _collect_gaps(lines, subtotal, slots)

    lines_clean = bool(lines) and subtotal > 0 and all(
        not (l.unresolved or l.ambiguous or l.below_moq or l.missing_price) for l in lines
    )
    rule_results: list[RuleResult] = []
    if lines_clean and rules:
        items = [
            EvalItem(
                product_id=l.product_id or "", sku=l.sku or "", product_name=l.product_name, quantity=l.cases,
                case_pack=l.case_pack, case_price=l.case_price, line_total=l.line_total,
                product=catalog.by_id.get(l.product_id or ""),
            )
            for l in lines if l.resolved
        ]
        ctx = POContext(discount_percentage=discount_pct(slots, subtotal), ship_to_country=ship_to_country, partnership_id=partnership_id)
        rule_results = evaluate_rules(ctx, items, rules, today=today)
    triggered = [r for r in rule_results if r.triggered]
    blocking = [r for r in triggered if r.severity == "block"]
    advisory = [r for r in triggered if r.severity != "block"]
    return CheckResult(
        lines=lines, subtotal=subtotal, gaps=gaps, blocking=blocking, advisory=advisory,
        rule_results=rule_results, ready=not gaps and not blocking,
    )
