"""Post-OCR pipeline: section heuristics, chunking, enrich NormalizedDocument.derived."""

from .chunking import enrich_normalized_document
from .extract_fields import (
    ContractExtractionDraft,
    FieldEvidenceEntry,
    build_recall_context_for_llm,
    extract_contract_fields,
    recall_chunks_for_field_group,
)

__all__ = [
    "ContractExtractionDraft",
    "FieldEvidenceEntry",
    "build_recall_context_for_llm",
    "enrich_normalized_document",
    "extract_contract_fields",
    "recall_chunks_for_field_group",
]
