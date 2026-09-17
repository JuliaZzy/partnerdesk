"""Environment → settings. Loads `<repo>/.env` once, never overriding real env vars.

Provider keys follow the model brand; feature knobs follow the capability. One
OpenAI-compatible endpoint covers OpenAI, Ollama and GLM — only base_url and model differ.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env = REPO_ROOT / ".env"
    if env.is_file():
        load_dotenv(env, override=False)


_load_dotenv()


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or "").strip() or default


@dataclass(frozen=True)
class LLMSettings:
    base_url: str | None
    api_key: str
    model: str
    # "schema" → strict json_schema response_format (OpenAI); "object" → json_object + shape
    # spelled out in the prompt (Ollama / GLM ignore json_schema).
    json_mode: str
    timeout: float = 60.0


def llm_settings(task: str | None = None) -> LLMSettings:
    """Global LLM config, with a per-task model override (`PO_EXTRACT_MODEL` etc.)."""
    base_url = env("LLM_BASE_URL") or None
    api_key = env("LLM_API_KEY") or env("OPENAI_API_KEY") or "ollama"
    model = env("LLM_MODEL") or "gpt-4.1"
    if task:
        model = env(f"{task.upper()}_MODEL") or model
    json_mode = env("LLM_JSON_MODE")
    if not json_mode:
        json_mode = "object" if base_url and "openai.com" not in base_url else "schema"
    return LLMSettings(base_url=base_url, api_key=api_key, model=model, json_mode=json_mode)


DB_PATH = Path(env("PARTNERDESK_DB", str(REPO_ROOT / "partnerdesk.sqlite3")))
FIXTURES_DIR = REPO_ROOT / "fixtures"
