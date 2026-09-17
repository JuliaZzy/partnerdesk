"""Gemini client for brand knowledge distillation.

Distillation reads whole decks — page images plus text — so it wants a long
multimodal context window, which is why this pipeline is on Gemini rather than
the OpenAI-compatible client the rest of the AI code uses.

Env follows the same split the contract OCR providers use: on Replit the keys are
injected under an `AI_INTEGRATIONS_` prefix; locally they come from the repo root
`.env` (see contract_ai/_env.py).
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import TypeVar

from pydantic import BaseModel

# Newest non-preview flash model available on this key (`client.models.list()`).
# Preview-suffixed models can change or be withdrawn, so they aren't the default.
# Override per-deploy with BRAND_KNOWLEDGE_MODEL.
DEFAULT_MODEL = "gemini-3.5-flash"

T = TypeVar("T", bound=BaseModel)


class GeminiNotConfigured(RuntimeError):
    """No API key available in this environment."""


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def resolve_model() -> str:
    return (os.environ.get("BRAND_KNOWLEDGE_MODEL") or DEFAULT_MODEL).strip()


def _api_key() -> str:
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        raise GeminiNotConfigured(
            "Set GEMINI_API_KEY (repo root .env locally, Replit secrets when deployed) "
            "to distill brand knowledge."
        )
    return key


def _client():
    from google import genai

    return genai.Client(api_key=_api_key())


DEFAULT_MAX_ATTEMPTS = 4

# Transport-level failures worth retrying. A distillation is many minutes of work
# across several calls, so a single dropped connection must not lose the whole run
# — corporate proxies and VPNs drop long-lived TLS to model APIs routinely.
_RETRYABLE_EXC_NAMES = {
    "ConnectError",
    "ConnectTimeout",
    "ReadError",
    "ReadTimeout",
    "WriteError",
    "PoolTimeout",
    "RemoteProtocolError",
    "ServerError",
}
_RETRYABLE_TEXT = ("ssl", "eof occurred", "timed out", "connection reset", "temporarily unavailable")


def _max_attempts() -> int:
    raw = (os.environ.get("BRAND_KNOWLEDGE_MAX_ATTEMPTS") or "").strip()
    try:
        return max(1, min(int(raw), 10))
    except ValueError:
        return DEFAULT_MAX_ATTEMPTS


def _is_retryable(err: Exception) -> bool:
    if type(err).__name__ in _RETRYABLE_EXC_NAMES:
        return True
    code = getattr(err, "code", None) or getattr(err, "status_code", None)
    if isinstance(code, int) and (code == 429 or 500 <= code < 600):
        return True
    text = str(err).lower()
    return any(marker in text for marker in _RETRYABLE_TEXT)


def _strip_code_fence(text: str) -> str:
    """Models occasionally wrap JSON in ```json fences despite the mime type."""
    s = text.strip()
    if not s.startswith("```"):
        return s
    s = s.split("\n", 1)[1] if "\n" in s else ""
    if s.rstrip().endswith("```"):
        s = s.rstrip()[: -3]
    return s.strip()


def generate_json(
    parts: list,
    *,
    system: str,
    schema: type[T],
    temperature: float = 0.2,
) -> T:
    """One structured call, retried on transport failures. Returns `schema` validated."""
    from google.genai import types

    client = _client()
    model = resolve_model()
    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_schema=schema,
        temperature=temperature,
    )

    attempts = _max_attempts()
    resp = None
    for attempt in range(1, attempts + 1):
        try:
            resp = client.models.generate_content(model=model, contents=parts, config=config)
            break
        except Exception as err:
            if attempt >= attempts or not _is_retryable(err):
                raise
            delay = min(2 ** (attempt - 1), 30)
            _log(
                f"[gemini] attempt {attempt}/{attempts} failed "
                f"({type(err).__name__}: {str(err)[:120]}); retrying in {delay}s"
            )
            time.sleep(delay)

    if resp is None:  # unreachable — the loop either breaks with a response or raises
        raise RuntimeError(f"No response from {model}.")

    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, schema):
        return parsed

    raw = _strip_code_fence(resp.text or "")
    if not raw:
        raise RuntimeError(f"Empty response from {model}.")
    return schema.model_validate(json.loads(raw))


def text_part(text: str):
    from google.genai import types

    return types.Part.from_text(text=text)


def image_part(data: bytes, mime_type: str):
    from google.genai import types

    return types.Part.from_bytes(data=data, mime_type=mime_type)
