"""File a contract the user has checked.

The chat turn only reads the agreement. This module writes it, using the edited filing
for the terms and the stored extraction for everything the panel does not show
(evidence, confidence, the heading tree). A second confirm updates the same contract.
"""

from __future__ import annotations

from typing import Any

from ..db import Database
from ..memory import record_history
from ..schemas import ContractChunk
from .ingest import ingest_extraction
from .turn import filing_table


def apply_filing(extraction: dict[str, Any], filing: dict[str, Any]) -> dict[str, Any]:
    """Overlay the fields the panel edits. Evidence and notes on the extraction stay."""
    out = dict(extraction)
    for key in ("contract_type", "term_start_date", "term_end_date", "exclusivity_type", "territory_text"):
        if key in filing:
            out[key] = _blank(filing.get(key))
    if filing.get("term_start_date"):
        out["effective_date"] = filing["term_start_date"]
    if "auto_renewal" in filing:
        out["auto_renewal"] = filing["auto_renewal"] if isinstance(filing["auto_renewal"], bool) else None
    if isinstance(filing.get("discount_rules"), list):
        out["commercial_discount_rules"] = [
            _discount(rule) for rule in filing["discount_rules"] if isinstance(rule, dict)
        ]
    if isinstance(filing.get("moqs"), list):
        out["minimum_order_quantities"] = [dict(row) for row in filing["moqs"] if isinstance(row, dict)]
    if isinstance(filing.get("price_list"), list):
        out["product_price_list"] = [dict(row) for row in filing["price_list"] if isinstance(row, dict)]
    if isinstance(filing.get("territories"), list):
        allowed: list[str] = []
        excluded: list[str] = []
        for row in filing["territories"]:
            if not isinstance(row, dict):
                continue
            code = str(row.get("country_code") or "").strip()
            if not code:
                continue
            (excluded if row.get("kind") == "excluded" else allowed).append(code)
        out["allowed_countries"] = allowed
        out["excluded_countries"] = excluded
    return out


def confirm_contract(db: Database, session_id: str, pending_id: str, filing: dict[str, Any]) -> dict[str, Any]:
    """Write the edited draft. The caller holds the session lock."""
    session = db.session(session_id)
    if not session:
        raise KeyError("session not found")
    if not isinstance(filing, dict):
        raise ValueError("contract filing is missing")
    history = [_copy(item) for item in (session.get("history") or [])]
    message = _find(history, pending_id)
    if message is None:
        raise KeyError("contract draft not found")
    pending = message.get("contract_pending") if isinstance(message.get("contract_pending"), dict) else {}
    extraction = pending.get("extraction") if isinstance(pending.get("extraction"), dict) else None
    if extraction is None:
        raise KeyError("contract draft not found")
    partnership = db.partnership(session["partnership_id"])
    if not partnership:
        raise KeyError("partnership not found")
    existed = isinstance(message.get("contract_id"), str) and bool(message.get("contract_id"))
    title = str(filing.get("title") or pending.get("title") or pending.get("file_name") or "Agreement").strip()[:200]
    rec = ingest_extraction(
        db, apply_filing(extraction, filing),
        brand_id=partnership.brand_id, partnership_id=session["partnership_id"],
        contract_id=message["contract_id"] if existed else None,
        title=title or "Agreement", file_name=pending.get("file_name"), file_sha256=pending.get("file_sha256"),
        model=pending.get("model"), chunks=_chunks(pending.get("chunks")),
    )
    table = filing_table(rec)
    table["pending_id"] = pending_id
    table["confirmed"] = True
    message["contract"] = table
    message["contract_id"] = rec.contract.id
    note = (
        "Updated the agreement. The Contracts page has the new terms."
        if existed else
        "Filed the agreement. The terms are on the Contracts page."
    )
    history.extend([{"role": "user", "content": "(confirm)"}, {"role": "assistant", "content": note}])
    record_history(db, session, history)
    return {"contract_id": rec.contract.id, "contract": table, "message": note}


def _find(history: list[Any], pending_id: str) -> dict[str, Any] | None:
    for message in history:
        if not isinstance(message, dict):
            continue
        pending = message.get("contract_pending") if isinstance(message.get("contract_pending"), dict) else {}
        filing = message.get("contract") if isinstance(message.get("contract"), dict) else {}
        if pending.get("id") == pending_id or filing.get("pending_id") == pending_id:
            return message
    return None


def _chunks(raw: Any) -> list[ContractChunk]:
    if not isinstance(raw, list):
        return []
    out: list[ContractChunk] = []
    for row in raw:
        if isinstance(row, dict) and row.get("chunk_id"):
            out.append(ContractChunk(**row))
    return out


def _discount(rule: dict[str, Any]) -> dict[str, Any]:
    tiers = []
    for tier in rule.get("tiers") or []:
        if isinstance(tier, dict):
            tiers.append({
                "from_container": tier.get("from_container"),
                "to_container": tier.get("to_container"),
                "discount_percent": tier.get("discount_percent"),
                "notes": tier.get("notes"),
            })
    return {
        "title": rule.get("title"),
        "section_reference": rule.get("section_reference"),
        "applies_per": rule.get("applies_per"),
        "basis": rule.get("basis"),
        "source_clause_text": rule.get("source_clause_text"),
        "tiers": tiers,
    }


def _blank(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _copy(item: Any) -> Any:
    return dict(item) if isinstance(item, dict) else item
