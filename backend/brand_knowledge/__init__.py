"""Brand-material retrieval.

`retrieve` is hybrid search over that brand's chunks, with an optional cross-encoder
rerank. `evaluate_retrieval` scores a labeled run. `answer` rewrites a follow-up
into a standalone query, then retrieves and answers. It is not an agent.
"""

from __future__ import annotations

from .rag import (
    RagUnavailable,
    chunks_from_pages,
    evaluate_retrieval,
    evidence_prompt,
    index_brand,
    retrieve,
)

__all__ = [
    "RagUnavailable",
    "chunks_from_pages",
    "evaluate_retrieval",
    "evidence_prompt",
    "index_brand",
    "retrieve",
]
