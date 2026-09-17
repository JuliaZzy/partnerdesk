"""stdin/stdout plumbing shared by the task CLIs.

Each task reads one JSON payload on stdin and prints exactly one JSON object as
the last stdout line — `{"ok": true, "result": ...}` or `{"ok": false,
"error": ...}` — which the Node caller parses with parseLastJsonLine(). Progress
or warnings must go to stderr so they never corrupt that last line.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def read_stdin_json() -> Any:
    """Parse the JSON payload Node wrote to our stdin."""
    return json.loads(sys.stdin.buffer.read().decode("utf-8"))


def emit(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.write("\n")


def emit_ok(result: Any) -> None:
    emit({"ok": True, "result": result})


def emit_err(message: str) -> None:
    emit({"ok": False, "error": message})
