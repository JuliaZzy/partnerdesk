"""
Phase-2 only: load NormalizedDocument from JSON (saved after OCR+chunk in Node), run
`extract_contract_fields` with keyword chunk recall + LLM — same engine as full Python pipeline.

  python -m contract_ai.tools.extract_contract_from_snapshot /path/to/artifact.json

Input JSON must match `load_normalized_document_json` (e.g. `{ "artifact": "chunk", "document": { ... } }`).
Stdout: one JSON object (same shape as `analyze_contract.py` success) for Node to parse.
"""

from __future__ import annotations

import json
import sys
import traceback

from contract_ai._env import load_python_dotenv


def main() -> None:
    load_python_dotenv()
    if len(sys.argv) < 2:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "Usage: extract_contract_from_snapshot <path-to-json>",
                }
            ),
            flush=True,
        )
        sys.exit(2)
    path = sys.argv[1].strip()
    if not path:
        print(json.dumps({"ok": False, "error": "Empty path"}), flush=True)
        sys.exit(2)

    try:
        from contract_ai.pipeline.extract_fields import extract_contract_fields
        from contract_ai.pipeline.load_normalized_document_json import load_normalized_document_json

        doc = load_normalized_document_json(path)
        draft = extract_contract_fields(doc, use_llm=True)
        extraction = json.loads(draft.model_dump_json())
        out = {
            "ok": True,
            "extraction": extraction,
            "document": {
                "full_text": doc.full_text or "",
                "pages": [
                    {
                        "page_number": p.page_number,
                        "text": p.text or "",
                    }
                    for p in doc.pages
                ],
            },
            "derived": {
                "sections": [s.model_dump() for s in (doc.derived.sections or [])],
                "chunks": [c.model_dump() for c in (doc.derived.chunks or [])],
            },
            "document_meta": {
                "page_count": len(doc.pages),
                "full_text_len": len(doc.full_text or ""),
                "ocr_provider_id": doc.ocr_provider_id,
            },
        }
        print(json.dumps(out, ensure_ascii=False), flush=True)
    except Exception as e:
        err = {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc(),
        }
        print(json.dumps(err, ensure_ascii=False), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
