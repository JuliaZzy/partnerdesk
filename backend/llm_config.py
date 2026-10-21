"""Which endpoint a dropdown choice calls.

The chat page sends one of these ids. The url and the call name live here, not in
`.env`. `.env` only holds the key (`LLM_API_KEY`).
"""

from __future__ import annotations

from dataclasses import dataclass

# Campus gateway. One url, four call names. Quota: 10 requests/min, 300000 tokens/min.
CAMPUS_BASE_URL = "https://models.sjtu.edu.cn/api/v1"


@dataclass(frozen=True)
class LLMConfig:
    """One row the dropdown can select."""

    id: str
    label: str
    short: str
    description: str
    base_url: str
    model: str
    aliases: tuple[str, ...] = ()
    thinking: bool = False
    vision: bool = False


LLM_CONFIGS: tuple[LLMConfig, ...] = (
    LLMConfig(
        id="deepseek-chat",
        label="DeepSeek V4 Flash",
        short="DeepSeek",
        description="General text. Fast, no chain-of-thought.",
        base_url=CAMPUS_BASE_URL,
        model="deepseek-chat",
    ),
    LLMConfig(
        id="deepseek-reasoner",
        label="DeepSeek V4 Flash (thinking)",
        short="Reasoner",
        description="Same Flash model, thinking on. Slower; thinking tokens count against the quota.",
        base_url=CAMPUS_BASE_URL,
        model="deepseek-reasoner",
        thinking=True,
    ),
    LLMConfig(
        id="minimax",
        label="MiniMax-M2.7",
        short="MiniMax",
        description="Agent-style tasks.",
        base_url=CAMPUS_BASE_URL,
        model="minimax",
        aliases=("minimax-m2.7",),
    ),
    LLMConfig(
        id="qwen",
        label="Qwen3.8-27B",
        short="Qwen",
        description="Vision and text. Use this to read order photos.",
        base_url=CAMPUS_BASE_URL,
        model="qwen",
        aliases=("qwen3.8-27b",),
        vision=True,
    ),
)

_THINKING_TIMEOUT = 180.0
_DEFAULT_TIMEOUT = 60.0


def chat_models() -> tuple[LLMConfig, ...]:
    return LLM_CONFIGS


def resolve_model_id(raw: str | None) -> str:
    """Map an alias to a dropdown id. An unknown name is returned unchanged."""
    wanted = (raw or "").strip()
    if not wanted:
        return LLM_CONFIGS[0].id
    key = wanted.lower()
    for item in LLM_CONFIGS:
        if key == item.id or key in item.aliases:
            return item.id
    return wanted


def get_llm_config(model_id: str | None) -> LLMConfig | None:
    if not model_id:
        return None
    key = model_id.strip().lower()
    for item in LLM_CONFIGS:
        if key == item.id or key in item.aliases:
            return item
    return None


def default_model_id() -> str:
    return LLM_CONFIGS[0].id


def model_sees_images(model_id: str | None) -> bool:
    """True for a catalog model that reads page and order photos. Unknown names do not."""
    spec = get_llm_config(model_id)
    return bool(spec and spec.vision)


def model_label(model_id: str) -> str:
    spec = get_llm_config(model_id)
    return spec.label if spec else model_id


def model_timeout(model_id: str) -> float:
    spec = get_llm_config(model_id)
    return _THINKING_TIMEOUT if spec and spec.thinking else _DEFAULT_TIMEOUT
