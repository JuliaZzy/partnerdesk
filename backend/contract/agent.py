"""Contract Analysis in chat. Files the agreement, then quotes the stored table.

`read` fills the form and `ingest` stores it. The reply does not recompute terms.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from ..db import Database
from ..llm import LLM
from ..orchestrator.speak import answer_directly
from .turn import file_contract


def _prompt(text: str, filed: dict[str, Any]) -> str:
    """The chat model quotes this table. It does not recompute the filing."""
    return (
        f"{text}\n\n"
        "=== CONTRACT FILING (from code — quote these figures, do not recompute or invent terms) ===\n"
        f"{json.dumps(filed, ensure_ascii=False)}\n"
        "If status is need_file, ask for the agreement file. If failed, say what went wrong. "
        "If filed, tell them what was stored, including skipped rows."
    )


def run(
    db: Database, llm: LLM, session: dict[str, Any], text: str, documents: list[tuple[str, bytes]],
) -> Iterator[dict[str, Any]]:
    db.update_session(session["id"], specialist="contract")
    filed = file_contract(db, llm, session, documents)
    yield {
        "type": "contract",
        "status": filed["status"],
        "contract_id": filed["contract_id"],
        "table": filed["table"],
    }
    yield from answer_directly(
        db, llm, session, text,
        prompt_text=_prompt(text, filed),
        extra={"contract_id": filed["contract_id"], "contract": filed["table"]},
    )
