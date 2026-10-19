"""Public web search via Bocha. Read-only colour for a report — not a source of our ledger.

https://api.bocha.cn/v1/web-search  (`BOCHA_API_KEY`)
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from ..config import env

BOCHA_URL = "https://api.bocha.cn/v1/web-search"
MAX_COUNT = 5

SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the public web for current information, news, or reports the model does not already know.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords"},
            },
            "required": ["query"],
        },
    },
}


def search(query: str, *, count: int = MAX_COUNT, api_key: str | None = None) -> list[dict[str, str]]:
    key = (api_key if api_key is not None else env("BOCHA_API_KEY")).strip()
    if not key:
        raise RuntimeError("BOCHA_API_KEY is not set")
    body = json.dumps({
        "query": query, "freshness": "noLimit", "summary": True, "count": max(1, min(count, MAX_COUNT)),
    }).encode()
    req = urllib.request.Request(
        BOCHA_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        raise RuntimeError(f"Bocha HTTP {err.code}") from err
    pages = (((payload.get("data") or {}).get("webPages") or {}).get("value")) or []
    out: list[dict[str, str]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        out.append({
            "title": str(page.get("name") or "").strip(),
            "snippet": str(page.get("snippet") or page.get("summary") or "").strip(),
            "url": str(page.get("url") or "").strip(),
        })
    return out


def run(arguments: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
    query = str(arguments.get("query") or "").strip()
    if not query:
        return {"ok": False, "data": None, "error": "query required"}
    try:
        return {"ok": True, "data": {"results": search(query)}, "error": None}
    except Exception as err:
        return {"ok": False, "data": None, "error": str(err)}
