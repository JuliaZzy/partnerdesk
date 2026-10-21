"""Environment → settings. Loads `<repo>/.env` once, never overriding real env vars.

Provider keys follow the model brand; feature knobs follow the capability. The chat
dropdown's url and call name live in `llm_config`, not in `.env`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .llm_config import LLM_CONFIGS as CHAT_MODELS
from .llm_config import (
    chat_models,
    default_model_id,
    model_timeout,
    resolve_model_id,
)
from .llm_config import get_llm_config as get_chat_model

__all__ = [
    "CHAT_MODELS",
    "chat_models",
    "default_model_id",
    "get_chat_model",
    "model_timeout",
    "resolve_model_id",
]

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
    """Key from the environment. Url and call name from `llm_config` for the chosen model."""
    model_id = default_model_id()
    if task:
        override = env(f"{task.upper()}_MODEL")
        if override:
            model_id = resolve_model_id(override)
    spec = get_chat_model(model_id)
    if spec is None:
        spec = CHAT_MODELS[0]
        call_name = model_id
    else:
        call_name = spec.model
    base_url = spec.base_url
    api_key = env("LLM_API_KEY") or env("OPENAI_API_KEY") or "ollama"
    json_mode = env("LLM_JSON_MODE")
    if not json_mode:
        json_mode = "object" if "openai.com" not in base_url else "schema"
    return LLMSettings(
        base_url=base_url, api_key=api_key, model=call_name, json_mode=json_mode, timeout=model_timeout(spec.id),
    )


@dataclass(frozen=True)
class EmbeddingSettings:
    """Not the chat model. Milvus only stores vectors; this endpoint computes them."""

    base_url: str | None
    api_key: str
    model: str
    dimensions: int


def embedding_settings() -> EmbeddingSettings:
    raw = env("EMBEDDING_DIMENSIONS") or "1024"
    try:
        dimensions = int(raw)
    except ValueError:
        dimensions = 1024
    return EmbeddingSettings(
        base_url=env("EMBEDDING_BASE_URL") or None,
        api_key=env("EMBEDDING_API_KEY") or env("LLM_API_KEY") or env("OPENAI_API_KEY") or "",
        model=env("EMBEDDING_MODEL") or "text-embedding-v4",
        dimensions=dimensions,
    )


@dataclass(frozen=True)
class RerankSettings:
    """Not the chat model and not the embedding model.

    ``BAAI/bge-reranker-v2-m3`` is a cross-encoder: one score for the question concatenated with
    one passage. It covers Chinese and English, and a batch of 20 short chunks is the size it
    is used at. The client speaks the Cohere-style `POST /rerank` body (`model`, `query`,
    `documents`, `top_n`). No base URL means hybrid retrieval returns the fused list and does
    not call the reranker.
    """

    base_url: str | None
    api_key: str
    model: str


def rerank_settings() -> RerankSettings:
    return RerankSettings(
        base_url=env("RERANK_BASE_URL") or None,
        api_key=env("RERANK_API_KEY") or "",
        model=env("RERANK_MODEL") or "BAAI/bge-reranker-v2-m3",
    )


def redis_url() -> str:
    """Redis, from `PARTNERDESK_REDIS_URL`. Compose injects `redis://redis:6379/0`."""
    url = env("PARTNERDESK_REDIS_URL")
    if not url:
        raise RuntimeError(
            "PARTNERDESK_REDIS_URL is not set. Run via ./dev (Docker Compose injects it). "
            "Inside the stack it is redis://redis:6379/0"
        )
    return url


def milvus_uri() -> str:
    """Milvus, from `MILVUS_URI`. Compose injects `http://milvus:19530`."""
    return env("MILVUS_URI")


def db_url() -> str:
    """The application database, from `PARTNERDESK_DB_URL`. PostgreSQL only.

    There is deliberately no fallback engine. A second engine means every schema decision
    has to hold on both, which is what kept timestamps as ISO-8601 text instead of
    `timestamptz`; and a silent fallback would let a misconfigured deploy come up on an
    empty database instead of failing. Missing config is an error, not a different mode."""
    url = env("PARTNERDESK_DB_URL")
    if not url:
        raise RuntimeError(
            "PARTNERDESK_DB_URL is not set. Run via ./dev (Docker Compose injects it). "
            "If you are inside a container without compose, set it to "
            "postgresql+psycopg://partnerdesk:partnerdesk@postgres:5432/partnerdesk"
        )
    return url
