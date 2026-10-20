"""OpenAI-compatible chat client.

Callers depend on the `LLM` protocol. `OpenAICompatLLM` talks to OpenAI, the
campus gateway, Ollama, or GLM through one SDK and one `base_url`.

In: a system prompt, messages, and either a JSON schema, a text stream, or tool
definitions. Out: a dict, text chunks, or one `AssistantTurn`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from typing import Any, Protocol

from ..config import LLMSettings, llm_settings, model_timeout, resolve_model_id
from .parse import parse_json_loose
from .types import AssistantTurn, Message, ToolCall


class LLM(Protocol):
    def complete_json(
        self,
        *,
        system: str,
        messages: list[Message],
        schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 900,
    ) -> dict[str, Any]: ...

    def stream_text(
        self,
        *,
        system: str,
        messages: list[Message],
        temperature: float = 0.7,
        max_tokens: int = 300,
    ) -> Iterator[str]: ...

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 900,
    ) -> AssistantTurn: ...


class OpenAICompatLLM:
    """OpenAI / campus / Ollama / GLM through the `openai` SDK — same chat API, different base_url."""

    def __init__(
        self,
        settings: LLMSettings | None = None,
        task: str | None = None,
        *,
        _client: Any | None = None,
    ):
        from openai import OpenAI

        self.settings = settings or llm_settings(task)
        if _client is not None:
            self._client = _client
            return
        kwargs: dict[str, Any] = {"api_key": self.settings.api_key, "timeout": self.settings.timeout, "max_retries": 1}
        if self.settings.base_url:
            kwargs["base_url"] = self.settings.base_url
        self._client = OpenAI(**kwargs)

    def with_model(self, model: str) -> OpenAICompatLLM:
        """Same HTTP client, different call name — used when the chat page switches models."""
        model = resolve_model_id(model)
        return OpenAICompatLLM(
            settings=replace(self.settings, model=model, timeout=model_timeout(model)),
            _client=self._client,
        )

    def _glm_extra(self) -> dict[str, Any]:
        # glm-4.x "thinking" occupies the stream (and can empty message.content). The
        # Python SDK rejects GLM-only fields at the top level — they ride in extra_body.
        url = (self.settings.base_url or "").lower()
        if "bigmodel.cn" in url or "zhipuai" in url:
            return {"extra_body": {"thinking": {"type": "disabled"}}}
        return {}

    def _response_format(self, schema: dict[str, Any] | None) -> dict[str, Any]:
        if schema and self.settings.json_mode == "schema":
            return {"type": "json_schema", "json_schema": schema}
        return {"type": "json_object"}

    def complete_json(self, *, system, messages, schema=None, temperature=0.0, max_tokens=900):
        resp = self._client.chat.completions.create(
            model=self.settings.model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=self._response_format(schema),
            messages=[{"role": "system", "content": system}, *messages],
            timeout=self.settings.timeout,
            **self._glm_extra(),
        )
        text = resp.choices[0].message.content or ""
        parsed = parse_json_loose(text)
        if not isinstance(parsed, dict):
            raise TypeError("model did not return a JSON object")
        return parsed

    def stream_text(self, *, system, messages, temperature=0.7, max_tokens=300):
        stream = self._client.chat.completions.create(
            model=self.settings.model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            messages=[{"role": "system", "content": system}, *messages],
            timeout=self.settings.timeout,
            **self._glm_extra(),
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            text = chunk.choices[0].delta.content
            if text:
                yield text

    def complete(self, *, system, messages, tools=None, temperature=0.0, max_tokens=900):
        kwargs: dict[str, Any] = {
            "model": self.settings.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, *messages],
            "timeout": self.settings.timeout,
            **self._glm_extra(),
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        resp = self._client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            raw = tc.function.arguments
            parsed: Any = {}
            if isinstance(raw, dict):
                parsed = raw
            elif isinstance(raw, str) and raw.strip():
                try:
                    parsed = parse_json_loose(raw)
                except json.JSONDecodeError:
                    parsed = {}
            args = parsed if isinstance(parsed, dict) else {}
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        return AssistantTurn(content=msg.content, tool_calls=calls)
