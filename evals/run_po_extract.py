"""Eval: slot extraction against golden cases, on a real model.

    python evals/run_po_extract.py [--cases evals/po_extract/cases.jsonl] [--model gpt-4.1]

Per case it calls `extract_turn` with the fixture catalog and compares the normalized
slots to `expect`. Line items match on (reference contains / contained by, cases);
`sku` is compared only when `expect` names it (null = "must not guess").
Prints per-field accuracy and the failing cases. Exit code 1 if anything failed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from partnerdesk.catalog import Catalog  # noqa: E402
from partnerdesk.llm import OpenAICompatLLM  # noqa: E402
from partnerdesk.models import Product  # noqa: E402
from partnerdesk.po.extract import extract_turn  # noqa: E402
from partnerdesk.po.slots import PoSlots  # noqa: E402


def load_catalog() -> Catalog:
    rows = json.loads((ROOT / "fixtures" / "products.json").read_text(encoding="utf-8"))
    return Catalog([Product(**r) for r in rows])


def lines_match(got: list[dict], exp: list[dict]) -> bool:
    if len(got) != len(exp):
        return False
    for g, e in zip(got, exp, strict=True):
        gr, er = g["reference"].lower(), e["reference"].lower()
        if not (er in gr or gr in er) or g["cases"] != e["cases"]:
            return False
        if "sku" in e and g.get("sku") != e["sku"]:
            return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(ROOT / "evals" / "po_extract" / "cases.jsonl"))
    ap.add_argument("--model")
    args = ap.parse_args()
    if args.model:
        os.environ["PO_EXTRACT_MODEL"] = args.model
    llm = OpenAICompatLLM(task="po_extract")
    catalog = load_catalog()
    cases = [json.loads(l) for l in Path(args.cases).read_text(encoding="utf-8").splitlines() if l.strip()]

    field_hits: dict[str, list[int]] = {}
    failures: list[tuple[str, str, object, object]] = []

    def score(field: str, ok: bool, cid: str, got: object, exp: object) -> None:
        field_hits.setdefault(field, []).append(int(ok))
        if not ok:
            failures.append((cid, field, got, exp))

    for c in cases:
        ex = extract_turn(llm, manifest=catalog.manifest(), current=PoSlots(**(c.get("current") or {})), history=[], message=c["message"])
        got = ex.slots.model_dump()
        exp = c["expect"]
        if "line_items" in exp:
            score("line_items", lines_match(got["line_items"], exp["line_items"]), c["id"], got["line_items"], exp["line_items"])
        for f in ("payment_terms", "incoterms", "shipping_method", "eta_date", "notes"):
            if f in exp:
                score(f, got[f] == exp[f], c["id"], got[f], exp[f])
        if "discount_kind" in exp:
            d = got["discount"] or {}
            ok = d.get("kind") == exp["discount_kind"] and (exp.get("discount_value") is None or d.get("value") == exp["discount_value"])
            score("discount", ok, c["id"], got["discount"], exp)
        if "discount_kind_not" in exp:
            score("discount(no-injection)", (got["discount"] or {}).get("kind") in (None, exp["discount_kind_not"]), c["id"], got["discount"], "not a discount")
        if "user_confirmed" in exp:
            score("user_confirmed", ex.user_confirmed == exp["user_confirmed"], c["id"], ex.user_confirmed, exp["user_confirmed"])
        if "reply_language" in exp:
            score("reply_language", ex.reply_language.lower().startswith(exp["reply_language"].lower()), c["id"], ex.reply_language, exp["reply_language"])

    print(f"model: {llm.settings.model}   cases: {len(cases)}")
    for f, hits in field_hits.items():
        print(f"  {f:24s} {sum(hits)}/{len(hits)}")
    for cid, f, got, exp in failures:
        print(f"FAIL {cid} · {f}\n   got: {json.dumps(got, ensure_ascii=False)}\n   exp: {json.dumps(exp, ensure_ascii=False)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
