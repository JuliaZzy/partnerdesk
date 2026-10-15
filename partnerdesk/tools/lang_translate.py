"""Translate a string. The model only changes language; it does not add or drop facts.

Chat understanding ("把刚刚的报告译成英语") belongs in the caller. This module only
resolves which string to translate, then makes one LLM call.
"""

from __future__ import annotations

from typing import Any, Literal

from ..llm import LLM

SYSTEM = """You're an expert translator. Translate the text the user provides into the requested language.
Output the translation only — no quotes, no 'here is the translation', no extra commentary.
Do not add facts that are not in the source. Keep names, SKUs and numbers as written unless the target language has a conventional form."""

Source = Literal["message", "last_assistant", "report"]
SOURCES = ("message", "last_assistant", "report")

# Shown to the model (OpenAI tools=). `run` reads the same keys. `text` is optional
# because source=report | last_assistant takes the string from session context.
SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "translate",
        "description": "Translate text into another language when the user asks for a translation.",
        "parameters": {
            "type": "object",
            "properties": {
                "target_language": {
                    "type": "string",
                    "description": "Language to translate into, e.g. English, Deutcher, 中文",
                },
                "text": {
                    "type": "string",
                    "description": "The exact words to translate, when they are in this user message",
                },
                "source": {
                    "type": "string",
                    "enum": list(SOURCES),
                    "description": "Where to get the text if `text` is empty",
                },
            },
            "required": ["target_language"],
        },
    },
}


def _source_key(raw: Any) -> Source:
    key = str(raw or "message").strip().lower().replace(" ", "_")
    if key not in SOURCES:
        raise ValueError(f"source must be one of {', '.join(SOURCES)}")
    return key  # type: ignore[return-value]


def resolve_text(arguments: dict[str, Any], context: dict[str, Any] | None) -> str:
    """Pick the string to translate. Explicit `text` wins; otherwise `source` names a
    field on `context` that the orchestrator already filled from the session."""
    explicit = str(arguments.get("text") or "").strip()
    if explicit:
        return explicit
    source = _source_key(arguments.get("source"))
    ctx = context or {}
    if source == "message":
        raise ValueError("text required when source is message")
    if source == "last_assistant":
        history = ctx.get("history") or []
        for turn in reversed(history):
            if isinstance(turn, dict) and turn.get("role") == "assistant":
                body = str(turn.get("content") or "").strip()
                if body:
                    return body
        raise ValueError("no assistant message in this session to translate")
    report = str(ctx.get("last_report") or "").strip()
    if not report:
        raise ValueError("no report in this session to translate")
    return report


def translate(llm: LLM, *, text: str, target_language: str) -> str:
    raw = llm.complete_json(
        system=SYSTEM,
        messages=[{
            "role": "user",
            "content": f"Target language: {target_language}\n\nText:\n{text}",
        }],
        schema={
            "name": "translation",
            "strict": False,
            "schema": {
                "type": "object",
                "properties": {"translation": {"type": "string"}},
                "required": ["translation"],
            },
        },
        temperature=0,
        max_tokens=800,
    )
    out = str(raw.get("translation") or "").strip()
    if not out:
        raise ValueError("empty translation")
    return out


def run(arguments: dict[str, Any], *, llm: LLM, context: dict[str, Any] | None = None) -> dict[str, Any]:
    target = str(arguments.get("target_language") or arguments.get("target_lang") or "").strip()
    if not target:
        return {"ok": False, "data": None, "error": "target_language required"}
    try:
        text = resolve_text(arguments, context)
        return {"ok": True, "data": {"translation": translate(llm, text=text, target_language=target)}, "error": None}
    except Exception as err:
        return {"ok": False, "data": None, "error": str(err)}
