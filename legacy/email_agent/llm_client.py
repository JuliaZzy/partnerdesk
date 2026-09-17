"""LLM client selection for the email agent — the Python mirror of
server/utils/openaiClient.ts.

Same provider routing as the TS side (both read the same repo-root .env):
- email agent (compose + reply triage): Replit -> OpenAI, local dev -> Ollama.
- lead research + business-card vision: z.ai (GLM) in every runtime.

GLM and Ollama both speak the OpenAI-compatible chat API, so the one `openai`
SDK client covers all three providers — only the base_url and model differ.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from openai import OpenAI

# "openai" | "glm" | "ollama" — controls response_format / extra_params below.
Provider = str


def is_replit_runtime() -> bool:
    return bool(
        os.environ.get("REPLIT_DEPLOYMENT")
        or os.environ.get("REPL_ID")
        or os.environ.get("REPLIT_DEV_DOMAIN")
        or os.environ.get("REPLIT_DOMAINS")
    )


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _get_openai() -> OpenAI:
    """Shared OpenAI client selection (aligned with openaiClient.ts getOpenAI)."""
    integration_key = _env("AI_INTEGRATIONS_OPENAI_API_KEY")
    integration_base = _env("AI_INTEGRATIONS_OPENAI_BASE_URL")
    local_key = _env("OPENAI_API_KEY")
    local_base = _env("OPENAI_BASE_URL")

    if is_replit_runtime():
        if integration_key:
            return OpenAI(
                api_key=integration_key,
                **({"base_url": integration_base} if integration_base else {}),
                timeout=60.0,
                max_retries=2,
            )
        if local_key:
            return OpenAI(
                api_key=local_key,
                **({"base_url": local_base} if local_base else {}),
                timeout=60.0,
                max_retries=2,
            )
        raise RuntimeError(
            "AI service not configured. On Replit set AI_INTEGRATIONS_OPENAI_API_KEY "
            "(AI Integrations) or OPENAI_API_KEY in Secrets."
        )

    return _ollama_client()


def _ollama_client() -> OpenAI:
    base = (
        _env("OLLAMA_BASE_URL")
        or _env("AI_INTEGRATIONS_OPENAI_BASE_URL")
        or "http://127.0.0.1:11434/v1"
    )
    key = _env("OLLAMA_API_KEY") or _env("AI_INTEGRATIONS_OPENAI_API_KEY") or "ollama"
    return OpenAI(api_key=key, base_url=base, timeout=60.0, max_retries=2)


def email_agent_config(task: str) -> tuple[OpenAI, str, Provider]:
    """Client for outreach compose + inbound reply triage.

    Different models per task: compose is customer-facing and needs multi-constraint
    judgment → gpt-5 (a reasoning model — worth its latency here, and compose is low
    volume). Reply triage wants deterministic temperature=0 output, which the gpt-5
    family forbids, so it uses gpt-4.1-mini (a non-reasoning model that keeps temp=0).
    Local dev uses Ollama for both. Override per task with AI_OUTREACH_MODEL / AI_TRIAGE_MODEL.
    """
    if is_replit_runtime():
        if task == "compose":
            model = _env("AI_OUTREACH_MODEL") or "gpt-5"
        else:  # "triage"
            model = _env("AI_TRIAGE_MODEL") or "gpt-4.1-mini"
        return _get_openai(), model, "openai"
    model = _env("OLLAMA_MODEL") or _env("AI_MAPPING_MODEL") or "llama3.2:3b"
    return _ollama_client(), model, "ollama"


def _glm_client() -> OpenAI:
    key = _env("ZHIPU_API_KEY")
    if not key:
        raise RuntimeError(
            "z.ai (GLM) not configured. Set ZHIPU_API_KEY (and optionally GLM_BASE_URL / GLM_MODEL)."
        )
    base = _env("GLM_BASE_URL") or "https://open.bigmodel.cn/api/paas/v4/"
    return OpenAI(api_key=key, base_url=base, timeout=60.0, max_retries=2)


def lead_research_config() -> tuple[OpenAI, str]:
    """Lead research — always z.ai (GLM), for its native server-side web_search."""
    model = _env("LEAD_RESEARCH_MODEL") or _env("GLM_MODEL") or "glm-4.6"
    return _glm_client(), model


def zai_config() -> tuple[OpenAI, str]:
    """Generic z.ai (GLM) client. Business-card vision overrides the model with GLM_VISION_MODEL."""
    model = _env("GLM_MODEL") or "glm-4.6"
    return _glm_client(), model


def response_format(provider: Provider, schema: dict[str, Any]) -> dict[str, Any]:
    """GLM/Ollama ignore strict json_schema, so they get the looser json_object mode."""
    if provider == "openai":
        return {"response_format": {"type": "json_schema", "json_schema": schema}}
    return {"response_format": {"type": "json_object"}}


def extra_params(provider: Provider) -> dict[str, Any]:
    """Disable glm-4.x "thinking" so message.content is plain JSON, not chain-of-thought.

    GLM-specific body fields must ride in `extra_body`: the Python openai SDK rejects
    unknown top-level kwargs (unlike the Node SDK, which forwards them verbatim).
    """
    if provider == "glm":
        return {"extra_body": {"thinking": {"type": "disabled"}}}
    return {}


_FENCE = re.compile(r"^```(?:json)?\s*([\s\S]*?)\s*```$", re.IGNORECASE)


def parse_json_loose(text: str) -> Any:
    """Parse a model's JSON answer, tolerating a ```json fence GLM sometimes adds."""
    trimmed = (text or "").strip()
    match = _FENCE.match(trimmed)
    return json.loads(match.group(1) if match else trimmed)


def extract_text(resp: Any) -> str:
    """Normalize an OpenAI-compatible chat response (incl. Ollama/GLM) to text."""
    try:
        choice = resp.choices[0]
    except (AttributeError, IndexError, TypeError):
        return ""
    message = getattr(choice, "message", None)
    raw = getattr(message, "content", None) if message is not None else None
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if isinstance(raw, list):
        parts: list[str] = []
        for part in raw:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and "text" in part:
                parts.append(str(part.get("text") or ""))
            else:
                text = getattr(part, "text", None)
                if text:
                    parts.append(str(text))
        joined = "".join(parts).strip()
        if joined:
            return joined
    refusal = getattr(message, "refusal", None) if message is not None else None
    if isinstance(refusal, str) and refusal.strip():
        return refusal.strip()
    return ""
