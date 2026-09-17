"""
Run OCR (or load from JSON) → optional chunk → optional extract; write stage artifacts.

  cd python
  pip install -r requirements.txt
  python -m contract_ai.tools.contract_stages path/to/contract.pdf --write-ocr out.ocr.json

OCR provider: CONTRACT_OCR_PROVIDER (default glm) or --provider once.

Resume without re-OCR:
  python -m contract_ai.tools.contract_stages --from-json out.ocr.json --chunk --write-chunk out.chunk.json

`requirements.txt` lives in the `python/` directory. Local secrets are loaded from the repo root `.env`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback

from contract_ai._env import load_python_dotenv


def _write_json(path: str, obj: object) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _artifact_wrap(stage: str, document: dict[str, object]) -> dict[str, object]:
    return {"artifact": stage, "document": document}


def _resolve_write_paths(
    args: argparse.Namespace,
) -> tuple[str | None, str | None, str | None]:
    wo, wc, we = args.write_ocr, args.write_chunk, args.write_extract
    if args.artifact_prefix:
        base = args.artifact_prefix.rstrip("/\\")
        wo = wo or f"{base}.ocr.json"
        wc = wc or f"{base}.chunk.json"
        we = we or f"{base}.extract.json"
    return wo, wc, we


def _run_steps_on_doc(
    doc,
    args: argparse.Namespace,
    *,
    write_ocr: str | None,
    write_chunk: str | None,
    write_extract: str | None,
) -> tuple[object, dict[str, object] | None]:
    from contract_ai.pipeline import enrich_normalized_document, extract_contract_fields

    extraction_out: dict[str, object] | None = None

    raw = json.loads(doc.model_dump_json())
    if write_ocr:
        _write_json(write_ocr, _artifact_wrap("ocr", raw))
        print(f"Wrote OCR artifact: {write_ocr}", file=sys.stderr)

    if args.chunk:
        doc = enrich_normalized_document(doc, enable_sections=not args.no_sections)
        raw = json.loads(doc.model_dump_json())
        if write_chunk:
            _write_json(write_chunk, _artifact_wrap("chunk", raw))
            print(f"Wrote chunk artifact: {write_chunk}", file=sys.stderr)

    if args.extract:
        draft = extract_contract_fields(doc, use_llm=True)
        extraction_out = json.loads(draft.model_dump_json())
        print(
            f"LLM extraction: confidence={draft.confidence_score}",
            file=sys.stderr,
        )
        if write_extract:
            _write_json(write_extract, {"artifact": "extract", "extraction": extraction_out})
            print(f"Wrote extract artifact: {write_extract}", file=sys.stderr)

    return doc, extraction_out


def main() -> None:
    load_python_dotenv()
    parser = argparse.ArgumentParser(
        description="OCR (or load JSON) → optional chunk → optional extract; write artifacts.",
    )
    parser.add_argument(
        "file",
        nargs="?",
        default=None,
        help="Path to PDF, image, or .docx (omit when using --from-json)",
    )
    parser.add_argument(
        "--provider",
        default=None,
        metavar="NAME",
        help="OCR provider once (default: CONTRACT_OCR_PROVIDER or glm): stub, textract, glm, openai",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Override CONTRACT_OCR_MAX_PAGES for PDF page limit",
    )
    parser.add_argument(
        "--artifact-prefix",
        metavar="PATH",
        default=None,
        help="Write three files: PATH.ocr.json, PATH.chunk.json, PATH.extract.json (steps that run)",
    )
    parser.add_argument("--write-ocr", metavar="PATH", default=None, help="Write NormalizedDocument after OCR only")
    parser.add_argument(
        "--write-chunk",
        metavar="PATH",
        default=None,
        help="Write NormalizedDocument after chunking (requires --chunk)",
    )
    parser.add_argument(
        "--write-extract",
        metavar="PATH",
        default=None,
        help="Write extraction JSON (requires --extract)",
    )
    parser.add_argument(
        "--chunk",
        action="store_true",
        help="After OCR, run chunking + optional section detection (fills derived.chunks / derived.sections)",
    )
    parser.add_argument(
        "--no-sections",
        action="store_true",
        help="With --chunk: only build chunks, skip heading-based sections",
    )
    parser.add_argument(
        "--extract",
        action="store_true",
        help="After OCR, call LLM to extract contract fields (OpenAI key required)",
    )
    parser.add_argument(
        "--from-json",
        "--extract-from-json",
        dest="from_json",
        metavar="PATH",
        default=None,
        help="Skip OCR: load NormalizedDocument from .ocr.json, .chunk.json, or legacy saved JSON",
    )
    args = parser.parse_args()

    if args.from_json and args.file:
        print("Use either a PDF/image file or --from-json, not both.", file=sys.stderr)
        sys.exit(2)
    if not args.from_json and not args.file:
        print("Provide a PDF, image, or .docx path, or --from-json PATH.", file=sys.stderr)
        sys.exit(2)

    if args.max_pages is not None:
        os.environ["CONTRACT_OCR_MAX_PAGES"] = str(args.max_pages)

    if args.provider:
        os.environ["CONTRACT_OCR_PROVIDER"] = args.provider.strip().lower()

    write_ocr, write_chunk, write_extract = _resolve_write_paths(args)

    from contract_ai.pipeline.load_normalized_document_json import load_normalized_document_json
    from contract_ai.ocr_providers.registry import get_ocr_provider_by_name

    if args.from_json:
        json_path = os.path.abspath(args.from_json)
        if not os.path.isfile(json_path):
            print(f"File not found: {json_path}", file=sys.stderr)
            sys.exit(1)
        try:
            doc = load_normalized_document_json(json_path)
        except Exception as e:
            print(f"Failed to load document from JSON: {e}", file=sys.stderr)
            sys.exit(1)

        print(f"\n{'=' * 60}\nSkipped OCR — loaded document from {json_path}\n{'=' * 60}", file=sys.stderr)
        try:
            doc, _extraction = _run_steps_on_doc(
                doc,
                args,
                write_ocr=None,
                write_chunk=write_chunk if args.chunk else None,
                write_extract=write_extract if args.extract else None,
            )
            ft = doc.full_text or ""
            n_pages = len(doc.pages)
            print(f"pages: {n_pages}  full_text_len: {len(ft)}", file=sys.stderr)
            if args.chunk:
                ns = len(doc.derived.sections)
                nc = len(doc.derived.chunks)
                print(f"derived: sections={ns}  chunks={nc}", file=sys.stderr)
        except Exception as e:
            print(f"FAILED: {type(e).__name__}: {e}", file=sys.stderr)
            print(traceback.format_exc(), file=sys.stderr)
            sys.exit(1)
        return

    path = os.path.abspath(args.file)
    if not os.path.isfile(path):
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)

    pname = (os.environ.get("CONTRACT_OCR_PROVIDER") or "glm").strip().lower()

    if path.lower().endswith(".docx"):
        print(f"\n{'=' * 60}\nDOCX — native text (no OCR)\n{'=' * 60}", file=sys.stderr)
        try:
            from contract_ai.pipeline.load_contract_for_analysis import load_contract_normalized_document

            doc = load_contract_normalized_document(path)
            doc, _extraction = _run_steps_on_doc(
                doc,
                args,
                write_ocr=write_ocr,
                write_chunk=write_chunk if args.chunk else None,
                write_extract=write_extract if args.extract else None,
            )
            ft = doc.full_text or ""
            n_pages = len(doc.pages)
            print(f"pages: {n_pages}  full_text_len: {len(ft)}", file=sys.stderr)
            if args.chunk:
                ns = len(doc.derived.sections)
                nc = len(doc.derived.chunks)
                print(f"derived: sections={ns}  chunks={nc}", file=sys.stderr)
            preview = (ft[:800] + "…") if len(ft) > 800 else ft
            print(preview or "(empty full_text — check pages[].text)", file=sys.stderr)
        except Exception as e:
            print(f"FAILED: {type(e).__name__}: {e}", file=sys.stderr)
            print(traceback.format_exc(), file=sys.stderr)
            sys.exit(1)
        return

    print(f"\n{'=' * 60}\nProvider: {pname}\n{'=' * 60}", file=sys.stderr)
    try:
        provider = get_ocr_provider_by_name(pname)
        doc = provider.ocr_document(path)
        doc, _extraction = _run_steps_on_doc(
            doc,
            args,
            write_ocr=write_ocr,
            write_chunk=write_chunk if args.chunk else None,
            write_extract=write_extract if args.extract else None,
        )
        ft = doc.full_text or ""
        n_pages = len(doc.pages)
        print(f"pages: {n_pages}  full_text_len: {len(ft)}", file=sys.stderr)
        if args.chunk:
            ns = len(doc.derived.sections)
            nc = len(doc.derived.chunks)
            print(f"derived: sections={ns}  chunks={nc}", file=sys.stderr)
        preview = (ft[:800] + "…") if len(ft) > 800 else ft
        print(preview or "(empty full_text — check pages[].text)", file=sys.stderr)
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
