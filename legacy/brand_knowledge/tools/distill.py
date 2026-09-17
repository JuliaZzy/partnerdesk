"""CLI: distil one brand material PDF into typed knowledge fragments.

    python -m brand_knowledge.tools.distill <pdf-path> [original-filename]

Prints exactly one JSON object on the last line of stdout:
    {"ok": true, "result": {...}}   |   {"ok": false, "error": "..."}

Progress goes to stderr, never stdout — the Node caller parses the last stdout
line. Mirrors contract_ai.tools.analyze_contract so both pipelines are spawned
and parsed the same way.
"""

from __future__ import annotations

import json
import sys

from contract_ai._env import load_python_dotenv

load_python_dotenv()

from ..distill import distill_document  # noqa: E402
from ..documents import UnsupportedDocument, load_document  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(json.dumps({"ok": False, "error": "Usage: distill <pdf-path> [original-filename]"}))
        return 2

    path = argv[1]
    try:
        doc = load_document(path)
        # The caller hands us a temp file; report the name the brand actually uploaded.
        if len(argv) > 2 and argv[2].strip():
            doc.filename = argv[2].strip()
        result = distill_document(doc)
    except UnsupportedDocument as err:
        print(json.dumps({"ok": False, "error": str(err)}))
        return 1
    except Exception as err:  # noqa: BLE001 — any failure must reach Node as JSON, not a traceback
        print(json.dumps({"ok": False, "error": f"{type(err).__name__}: {err}"}))
        return 1

    print(json.dumps({"ok": True, "result": result.model_dump()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
