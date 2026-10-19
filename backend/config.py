"""Environment → settings. Loads `<repo>/.env` once, never overriding real env vars.

Provider keys follow the model brand; feature knobs follow the capability. One
OpenAI-compatible endpoint covers OpenAI, SJTU campus, Ollama and GLM — only base_url and model differ.
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
class ChatModel:
    """One selectable campus model. `id` is the API call name; `aliases` are also accepted."""

    id: str
    label: str
    description: str
    aliases: tuple[str, ...] = ()
    thinking: bool = False
    vision: bool = False
    short: str = ""


# SJTU campus catalog — same endpoint, four call names. The chat page lists these.
CHAT_MODELS: tuple[ChatModel, ...] = (
    ChatModel(
        id="deepseek-chat",
        label="DeepSeek V4 Flash",
        short="DeepSeek",
        description="General text. Fast, no chain-of-thought.",
    ),
    ChatModel(
        id="deepseek-reasoner",
        label="DeepSeek V4 Flash (thinking)",
        short="Reasoner",
        description="Deep reasoning. Slower; thinking tokens count against the quota.",
        thinking=True,
    ),
    ChatModel(
        id="minimax",
        label="MiniMax-M2.7",
        short="MiniMax",
        description="Agent-style tasks.",
        aliases=("minimax-m2.7",),
    ),
    ChatModel(
        id="qwen",
        label="Qwen3.8-27B",
        short="Qwen",
        description="Vision and text. Use this to read order photos.",
        aliases=("qwen3.8-27b",),
        vision=True,
    ),
)

_THINKING_TIMEOUT = 180.0
_DEFAULT_TIMEOUT = 60.0


def resolve_model_id(raw: str | None) -> str:
    """Map an alias or env value to a catalog id; unknown names pass through."""
    wanted = (raw or "").strip()
    if not wanted:
        return CHAT_MODELS[0].id
    key = wanted.lower()
    for m in CHAT_MODELS:
        if key == m.id or key in m.aliases:
            return m.id
    return wanted


def get_chat_model(model_id: str) -> ChatModel | None:
    key = model_id.strip().lower()
    for m in CHAT_MODELS:
        if key == m.id or key in m.aliases:
            return m
    return None


def chat_models() -> tuple[ChatModel, ...]:
    """Campus catalog, plus `LLM_MODEL` when it is not already one of the four."""
    extra = env("LLM_MODEL")
    if extra and resolve_model_id(extra) not in {m.id for m in CHAT_MODELS}:
        return (*CHAT_MODELS, ChatModel(id=extra, label=extra, description="From LLM_MODEL"))
    return CHAT_MODELS


def default_model_id() -> str:
    return resolve_model_id(env("LLM_MODEL"))


def model_sees_images(model_id: str | None) -> bool:
    """True for a catalog model that reads page and order photos. Unknown names do not."""
    spec = get_chat_model(model_id) if model_id else None
    return bool(spec and spec.vision)


def model_label(model_id: str) -> str:
    spec = get_chat_model(model_id)
    return spec.label if spec else model_id


def model_timeout(model_id: str) -> float:
    spec = get_chat_model(model_id)
    return _THINKING_TIMEOUT if spec and spec.thinking else _DEFAULT_TIMEOUT


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
    model = default_model_id()
    if task:
        override = env(f"{task.upper()}_MODEL")
        if override:
            model = resolve_model_id(override)
    json_mode = env("LLM_JSON_MODE")
    if not json_mode:
        json_mode = "object" if base_url and "openai.com" not in base_url else "schema"
    return LLMSettings(
        base_url=base_url, api_key=api_key, model=model, json_mode=json_mode, timeout=model_timeout(model),
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
