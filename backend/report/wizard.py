"""Run a short Python snippet over this draft's rows and keep the insights it assigns.

The model decides the arithmetic. The process only supplies the rows, blocks imports
and private names, and stops the snippet if it runs too long.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from typing import Any

_BANNED = frozenset({
    "exec", "eval", "open", "compile", "__import__", "input", "breakpoint",
    "getattr", "setattr", "delattr", "globals", "locals", "vars", "dir",
    "help", "exit", "quit", "memoryview", "bytearray", "bytes",
})
_BANNED_NODES = (
    ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal, ast.ClassDef,
    ast.With, ast.AsyncWith, ast.AsyncFunctionDef, ast.Await,
)
_MAX_CODE = 6_000
_MAX_INSIGHTS = 12
_TIMEOUT = 5

# The child is isolated. Restricted builtins stay here so a missed syntax check still cannot import.
_RUNNER = r"""
import json, sys
data = json.loads(sys.stdin.read() or "{}")
safe = {
    "abs": abs, "min": min, "max": max, "sum": sum, "len": len, "range": range,
    "enumerate": enumerate, "round": round, "sorted": sorted, "zip": zip,
    "map": map, "filter": filter, "list": list, "dict": dict, "set": set, "tuple": tuple,
    "str": str, "int": int, "float": float, "bool": bool, "isinstance": isinstance,
    "any": any, "all": all, "reversed": reversed, "callable": callable,
}
namespace = {"datasets": data.get("datasets") or [], "insights": []}
try:
    import math, statistics, json as jsonlib, collections, re, datetime
    namespace.update({
        "math": math, "statistics": statistics, "json": jsonlib,
        "collections": collections, "re": re, "datetime": datetime,
    })
except Exception:
    pass
try:
    import pandas as pd
    namespace["pd"] = pd
    namespace["pandas"] = pd
except Exception:
    pass
try:
    exec(data.get("code") or "", {"__builtins__": safe}, namespace)
    fn = namespace.get("analyze")
    insights = fn(namespace["datasets"]) if callable(fn) else namespace.get("insights")
    json.dump({"ok": True, "insights": insights}, sys.stdout)
except Exception as err:
    json.dump({"ok": False, "error": f"{type(err).__name__}: {err}"[:300]}, sys.stdout)
"""


def reject_code(code: str) -> str | None:
    """A reason to refuse the snippet, or None when it only does arithmetic on the rows."""
    if not isinstance(code, str) or not code.strip():
        return "no code"
    if len(code) > _MAX_CODE:
        return "code is too long"
    try:
        tree = ast.parse(code)
    except SyntaxError as err:
        return f"syntax error: {err.msg}"
    for node in ast.walk(tree):
        if isinstance(node, _BANNED_NODES):
            return "imports and private runtime access are not allowed"
        if isinstance(node, ast.Name) and (node.id in _BANNED or node.id.startswith("_")):
            return f"name is not allowed: {node.id}"
        if isinstance(node, ast.Attribute) and str(node.attr).startswith("_"):
            return "private attributes are not allowed"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _BANNED:
            return f"call is not allowed: {node.func.id}"
    return None


def execute(code: str, datasets: list[dict[str, Any]]) -> dict[str, Any]:
    """Run code with datasets bound. Returns insights the snippet assigned, or an error."""
    reason = reject_code(code)
    if reason:
        return {"insights": [], "error": reason}
    payload = json.dumps({"code": code, "datasets": _plain(datasets)}, ensure_ascii=False, default=str)
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-c", _RUNNER],
            input=payload, capture_output=True, text=True, timeout=_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {"insights": [], "error": "analysis timed out"}
    if proc.returncode != 0:
        detail = (proc.stderr or "analysis failed").strip().split("\n", 1)[0][:300]
        return {"insights": [], "error": detail}
    try:
        raw = json.loads(proc.stdout or "")
    except json.JSONDecodeError:
        return {"insights": [], "error": "analysis did not return insights"}
    if not isinstance(raw, dict) or not raw.get("ok"):
        return {"insights": [], "error": str((raw or {}).get("error") or "analysis failed")[:300]}
    return {"insights": _insights(raw.get("insights")), "error": None}


def _plain(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return []


def _insights(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    kept: list[dict[str, Any]] = []
    for item in value[:_MAX_INSIGHTS]:
        if isinstance(item, str) and item.strip():
            kept.append({"title": item.strip()[:80], "detail": item.strip()[:500], "evidence": {}})
            continue
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()[:80]
        detail = str(item.get("detail") or "").strip()[:500]
        if not title and not detail:
            continue
        kept.append({"title": title, "detail": detail, "evidence": _plain(item.get("evidence"))})
    return kept
