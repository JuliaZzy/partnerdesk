"""Agreement filing, kept in one package.

`read` fills the extraction form. `chunks` splits an agreement on its headings.
`ingest` stores every term and derives PO rules in code. `turn` is the chat entry.
The model does not decide whether a term applies.
"""

from __future__ import annotations

from .chunks import split_chunks
from .ingest import contract_discount_for_next_order, ingest_extraction, record_from_extraction
from .read import read_agreement, read_and_ingest, sanitize_extraction, summarize
from .turn import run_contract_turn

__all__ = [
    "contract_discount_for_next_order",
    "ingest_extraction",
    "read_agreement",
    "read_and_ingest",
    "record_from_extraction",
    "run_contract_turn",
    "sanitize_extraction",
    "split_chunks",
    "summarize",
]
