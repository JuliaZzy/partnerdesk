"""CLI: read photographed report pages into a cell grid via GLM vision.

    {"pages": ["data:image/jpeg;base64,...", ...]} on stdin
      | python -m email_agent.tasks.read_report_photo

Prints {"ok": true, "result": {"sheets": [{"name": "Page 1", "rows": [[...]]}]}}.

`rows` deliberately matches the shape XLSX's `sheet_to_json({header: 1})` gives
the sales-import wizard, so a scanned page rejoins the existing pipeline
(detectMonthlyMatrix → header detection → SKU matching) with no special casing.

Every cell comes back as a string. A photograph has no cell types to recover, and
the wizard already parses numerics out of strings itself, so typing them here
would only invent precision the image does not carry.

One vision call per page: a page that fails is skipped with a warning on stderr
rather than losing the whole scan.
"""

from __future__ import annotations

import os
import sys

from contract_ai._env import load_python_dotenv

load_python_dotenv()

from .._io import emit_err, emit_ok, read_stdin_json  # noqa: E402
from ..llm_client import extra_params, extract_text, parse_json_loose, zai_config  # noqa: E402

# Bounds the cost and runtime of one scan. VNDocumentCameraViewController will
# happily return dozens of pages; a sales report that needs more than this is a
# spreadsheet, not a photograph.
MAX_PAGES = 10

PROMPT = """You are transcribing a photographed sales or stock report into a table.
Return ONLY a JSON object with exactly this key (no prose, no markdown fence):
{
  "rows": [["cell", "cell"], ["cell", "cell"]]
}
Rules, in order of importance:
1. The FIRST entry of "rows" MUST be the table's column headings, exactly as
   printed (for example ["SKU", "Product Name", "Units Sold", "Revenue"]).
   This is required. Never start with a data row. If the table genuinely has no
   printed headings, use "" for each one so the first row is still the heading
   row and the data starts at the second.
2. Then one entry per data row, top to bottom, left to right.
3. Copy every value exactly as printed, including thousands separators, currency
   symbols and percent signs. Do not round, convert, total or reformat anything.
4. Pad every row to the same number of columns using "" for empty cells.
5. Use "" for a cell that is blank or illegible. Never guess a value, and never
   add a row, column or figure that is not printed on the page.
6. Skip total, subtotal and grand-total rows. They are summaries of the rows
   above, not products, and they are recalculated later from the data rows.
7. Ignore page furniture that is not part of the table: titles, logos, page
   numbers, headers, footers and handwritten annotations.
8. If the page has no tabular data at all, return {"rows": []}."""


def read_page(image_data_url: str) -> list[list[str]]:
    """Vision model transcribes one page. REPORT_SCAN_MODEL overrides the model."""
    client, _ = zai_config()
    model = (
        (os.environ.get("REPORT_SCAN_MODEL") or "").strip()
        or (os.environ.get("GLM_VISION_MODEL") or "").strip()
        or "glm-4v-flash"
    )

    completion = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            }
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        **extra_params("glm"),
    )

    raw = extract_text(completion)
    if not raw:
        raise ValueError("The page reader returned nothing.")
    parsed = parse_json_loose(raw)
    rows = parsed.get("rows") if isinstance(parsed, dict) else None
    if not isinstance(rows, list):
        raise ValueError("The page reader did not return a rows array.")

    # Normalize to a rectangular grid of strings. The model is asked to pad, but
    # a short row would otherwise shift the wizard's column alignment silently.
    grid = [[_cell(c) for c in row] for row in rows if isinstance(row, list)]
    width = max((len(r) for r in grid), default=0)
    return [r + [""] * (width - len(r)) for r in grid]


def _cell(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "null" else text


def main() -> int:
    try:
        payload = read_stdin_json()
    except Exception as err:  # noqa: BLE001
        emit_err(f"Invalid stdin JSON: {err}")
        return 1

    pages = payload.get("pages") if isinstance(payload, dict) else None
    if not isinstance(pages, list) or not pages:
        emit_err("No pages provided.")
        return 1
    pages = [p for p in pages if isinstance(p, str) and p][:MAX_PAGES]
    if not pages:
        emit_err("No pages provided.")
        return 1

    sheets: list[dict[str, object]] = []
    failures: list[str] = []
    for index, page in enumerate(pages, start=1):
        try:
            rows = read_page(page)
        except Exception as err:  # noqa: BLE001 — one bad page must not lose the rest
            print(f"[read_report_photo] page {index} failed: {err}", file=sys.stderr)
            failures.append(f"page {index}")
            continue
        if rows:
            sheets.append({"name": f"Page {index}", "rows": rows})

    if not sheets:
        emit_err(
            "Could not read a table from that scan."
            + (f" Failed: {', '.join(failures)}." if failures else "")
        )
        return 1

    emit_ok({"sheets": sheets})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
