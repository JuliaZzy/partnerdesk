"""通过 Frankfurter（欧洲央行）查汇率。不需要 API key。

https://api.frankfurter.app/latest?amount=1&from=CNY&to=EUR
只读的背景——不是我们已经存着的账本总额的来源。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlencode

FRANKFURTER_URL = "https://api.frankfurter.app/latest"

# Common aliases the model may emit; Frankfurter wants ISO codes.
_ALIASES = {
    "RMB": "CNY",
    "CNH": "CNY",
    "DOLLAR": "USD",
    "EURO": "EUR",
    "YEN": "JPY",
    "POUND": "GBP",
}

SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "exchange_rate",
        "description": (
            "查询汇率（欧洲央行，经由 Frankfurter）。"
            "用于货币换算的问题；不要编造汇率。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "base_currency": {
                    "type": "string",
                    "description": "源货币的 ISO 代码，例如 CNY、USD、EUR（也接受 RMB）",
                },
                "target_currency": {
                    "type": "string",
                    "description": "目标货币的 ISO 代码，例如 EUR、USD、CNY",
                },
                "amount": {
                    "type": "number",
                    "description": "要换算的源货币金额（默认 1）",
                },
            },
            "required": ["base_currency", "target_currency"],
        },
    },
}


def _code(raw: Any) -> str:
    text = str(raw or "").strip().upper()
    return _ALIASES.get(text, text)


def quote(base: str, target: str, *, amount: float = 1.0) -> dict[str, Any]:
    base_c, target_c = _code(base), _code(target)
    if not base_c or not target_c:
        raise ValueError("base_currency and target_currency required")
    if base_c == target_c:
        return {
            "base": base_c, "target": target_c, "amount": amount,
            "rate": 1.0, "converted": amount, "date": None, "source": "identity",
        }
    url = f"{FRANKFURTER_URL}?{urlencode({'amount': amount, 'from': base_c, 'to': target_c})}"
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        raise RuntimeError(f"Frankfurter HTTP {err.code}") from err
    rates = payload.get("rates") or {}
    rate = rates.get(target_c)
    if rate is None:
        raise RuntimeError(f"no rate for {base_c}->{target_c}; check currency codes")
    amt = float(payload.get("amount") or amount)
    converted = float(rate)
    # Frankfurter returns the converted amount in rates[target] when amount= is set.
    unit = converted / amt if amt else converted
    return {
        "base": base_c,
        "target": target_c,
        "amount": amt,
        "rate": unit,
        "converted": converted,
        "date": payload.get("date"),
        "source": "European Central Bank (Frankfurter)",
    }


def run(arguments: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
    base = arguments.get("base_currency")
    target = arguments.get("target_currency")
    if not str(base or "").strip() or not str(target or "").strip():
        return {"ok": False, "data": None, "error": "base_currency and target_currency required"}
    try:
        amount = float(arguments.get("amount") if arguments.get("amount") is not None else 1)
    except (TypeError, ValueError):
        return {"ok": False, "data": None, "error": "amount must be a number"}
    if amount <= 0:
        return {"ok": False, "data": None, "error": "amount must be positive"}
    try:
        return {"ok": True, "data": quote(str(base), str(target), amount=amount), "error": None}
    except Exception as err:
        return {"ok": False, "data": None, "error": str(err)}
