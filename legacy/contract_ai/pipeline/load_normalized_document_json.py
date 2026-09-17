"""Load NormalizedDocument from saved pipeline JSON (OCR / chunk artifacts)."""

from __future__ import annotations

import json
from typing import Any

from ..schemas import NormalizedDocument


def load_normalized_document_json(path: str) -> NormalizedDocument:
    """
    Load NormalizedDocument from:
    - { "artifact": "ocr"|"chunk", "document": { ... } }
    - { "document": { ... } }
    - raw NormalizedDocument at top level (schema_version + pages)
    - legacy multi-provider wrapper: { "providers": { "glm": { "ok": true, "document": ... } } }
    """
    with open(path, encoding="utf-8") as f:
        data: Any = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")

    doc_data = data.get("document")
    if doc_data is None:
        if data.get("schema_version") and "pages" in data:
            doc_data = data
    if doc_data is None:
        providers = data.get("providers") or {}
        for _name, entry in providers.items():
            if isinstance(entry, dict) and entry.get("ok") and entry.get("document"):
                doc_data = entry["document"]
                break
    if not doc_data:
        raise ValueError(
            f"No document found in {path} (expected document key, NormalizedDocument root, or providers.*.document)"
        )
    return NormalizedDocument.model_validate(doc_data)
