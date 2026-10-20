"""File an agreement from chat and hand the stored result back as a table.

The chat model does not file and does not invent terms. This module runs `read` (which
splits chunks) and `ingest`, then returns the rows that were written. `agent` is
what speaks to the distributor.
"""

from __future__ import annotations

from typing import Any

from ..db import Database
from ..documents import SUPPORTED_EXTS
from ..llm import LLM
from .read import read_and_ingest


def file_contract(
    db: Database, llm: LLM, session: dict[str, Any], documents: list[tuple[str, bytes]],
) -> dict[str, Any]:
    """Read and store the first agreement, or report why nothing was written.

    `table` is built from the stored record. A filename without bytes never reaches here:
    the caller only passes documents that have content.
    """
    readable = [(name, data) for name, data in documents if name.lower().endswith(SUPPORTED_EXTS)]
    if not readable:
        return {"status": "need_file", "contract_id": None, "table": None, "error": None}
    name, data = readable[0]
    try:
        rec = read_and_ingest(
            db, llm,
            brand_id=_brand_id(db, session["partnership_id"]),
            partnership_id=session["partnership_id"],
            file_name=name, data=data,
        )
    except Exception as err:
        return {"status": "failed", "contract_id": None, "table": None, "error": str(err)}
    table = _filing_table(rec)
    unread = [other for other, _ in readable[1:]]
    if unread:
        table["unread_files"] = unread
    return {"status": "filed", "contract_id": rec.contract.id, "table": table, "error": None}


def _brand_id(db: Database, partnership_id: str) -> str:
    partnership = db.partnership(partnership_id)
    if not partnership:
        raise KeyError("partnership not found")
    return partnership.brand_id


def _filing_table(rec: Any) -> dict[str, Any]:
    """Rows the chat model may quote. Every figure comes from the stored record."""
    contract, terms = rec.contract, rec.terms
    return {
        "contract_id": contract.id,
        "title": contract.title,
        "file_name": contract.file_name,
        "contract_type": contract.contract_type,
        "term_start_date": contract.term_start_date,
        "term_end_date": contract.term_end_date,
        "auto_renewal": contract.auto_renewal,
        "exclusivity_type": contract.exclusivity_type,
        "territory_text": contract.territory_text,
        "discount_rules": [
            {
                "title": rule.title,
                "section_reference": rule.section_reference,
                "applies_per": rule.applies_per,
                "basis": rule.basis,
                "tiers": [
                    {
                        "from_container": tier.from_container,
                        "to_container": tier.to_container,
                        "discount_percent": tier.discount_percent,
                    }
                    for tier in rule.tiers
                ],
            }
            for rule in terms.discount_rules
        ],
        "moqs": [row.model_dump(exclude_none=True) for row in terms.moqs],
        "territories": [row.model_dump() for row in terms.territories],
        "price_list": [row.model_dump(exclude_none=True) for row in terms.price_list],
        "derived_rules": [
            {
                "rule_type": rule.rule_type,
                "name": rule.name,
                "severity": rule.severity,
                "rule_config": rule.rule_config,
            }
            for rule in rec.derived_rules
        ],
        "skipped": list(rec.skipped),
    }
