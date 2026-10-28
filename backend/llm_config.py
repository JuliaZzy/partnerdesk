"""Which endpoint a dropdown choice calls.

The chat page sends one of these ids. The url and the call name live here, not in
`.env`. `.env` only holds the key (`LLM_API_KEY`).
"""

from __future__ import annotations

import os
from dotenv import load_dotenv
from dataclasses import dataclass


load_dotenv(os.path.join(os.path.dirname(__file__), '.env'), override=False)


@dataclass(frozen=True)
class LLMConfig:
    """One row the dropdown can select."""

    id: str
    description: str
    base_url: str
    model: str
    thinking: bool = False


LLM_CONFIGS: tuple[LLMConfig, ...] = (
    LLMConfig(
        id="deepseek-chat",
        description="General text. Fast, no chain-of-thought.",
        base_url=os.getenv("SJTU_BASE_URL"),
        model="deepseek-chat",
    ),
    LLMConfig(
        id="deepseek-reasoner",
        description="Same Flash model, thinking on. Slower; thinking tokens count against the quota.",
        base_url=os.getenv("SJTU_BASE_URL"),
        model="deepseek-reasoner",
        thinking=True,
    ),
    LLMConfig(
        id="minimax",
        description="Agent-style tasks.",
        base_url=os.getenv("SJTU_BASE_URL"),
        model="minimax",
    ),
    LLMConfig(
        id="qwen",
        description="Vision and text. Use this to read order photos.",
        base_url=os.getenv("SJTU_BASE_URL"),
        model="qwen",
    ),
)