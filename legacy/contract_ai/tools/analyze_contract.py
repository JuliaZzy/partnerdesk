"""
One-shot pipeline for Node: load contract (DOCX = native text; PDF/images = OCR) → chunk → LLM extract → JSON on stdout.

  cd python
  python -m contract_ai.tools.analyze_contract /path/to/contract.pdf
  python -m contract_ai.tools.analyze_contract /path/to/contract.docx

Requires: OPENAI_API_KEY (or AI_INTEGRATIONS_OPENAI_API_KEY) for field extraction; PDF/images OCR needs ZHIPU_API_KEY when CONTRACT_OCR_PROVIDER=glm (default), or OpenAI keys when using openai.
DOCX does not use vision OCR — only the extraction LLM call needs the OpenAI key.
Optional: CONTRACT_OCR_PROVIDER=glm|openai|textract|stub (PDF/images only). Loads the repo root `.env`.
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
            json.dumps({"ok": False, "error": "Usage: analyze_contract <path-to-file> [--ocr-only]"}),
            flush=True,
        )
        sys.exit(2)
    path = sys.argv[1].strip()
    if not path:
        print(json.dumps({"ok": False, "error": "Empty path"}), flush=True)
        sys.exit(2)
        
    ocr_only = "--ocr-only" in sys.argv

    try:
        from contract_ai.pipeline import enrich_normalized_document, extract_contract_fields
        from contract_ai.pipeline.load_contract_for_analysis import load_contract_normalized_document

        doc = load_contract_normalized_document(path)
        doc = enrich_normalized_document(doc, enable_sections=True)
        draft = extract_contract_fields(doc, use_llm=not ocr_only)
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
        err = {"ok": False, "error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}
        print(json.dumps(err, ensure_ascii=False), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
