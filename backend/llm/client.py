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

from ..config import LLMSettings, get_chat_model, llm_settings, model_timeout
from ..llm_config import get_llm_config
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
        model_outputs: list[dict[str, Any]] | None = None,
    ):
        from openai import OpenAI

        self.settings = settings or llm_settings(task)
        # One list for this turn. with_model keeps the same list so a dropdown switch
        # still records every JSON object the desk shows.
        self.model_outputs = [] if model_outputs is None else model_outputs
        if _client is not None:
            self._client = _client
            return
        kwargs: dict[str, Any] = {"api_key": self.settings.api_key, "timeout": self.settings.timeout, "max_retries": 1}
        if self.settings.base_url:
            kwargs["base_url"] = self.settings.base_url
        self._client = OpenAI(**kwargs)

    def with_model(self, model: str) -> OpenAICompatLLM:
        """Dropdown choice: that row's url and call name. Same url keeps this HTTP client."""
        spec = get_llm_config(model)
        if spec is None:
            chosen = (model or "").strip()
            return OpenAICompatLLM(
                settings=replace(self.settings, model=chosen, timeout=model_timeout(chosen)),
                _client=self._client,
                model_outputs=self.model_outputs,
            )
        settings = replace(
            self.settings, base_url=spec.base_url, model=spec.model, timeout=model_timeout(spec.id),
        )
        same_endpoint = settings.base_url == self.settings.base_url
        return OpenAICompatLLM(
            settings=settings, _client=self._client if same_endpoint else None, model_outputs=self.model_outputs,
        )

    def _request_extra(self) -> dict[str, Any]:
        """Provider fields the OpenAI SDK rejects as top-level kwargs."""
        url = (self.settings.base_url or "").lower()
        body: dict[str, Any] = {}
        # glm-4.x "thinking" occupies the stream (and can empty message.content).
        if "bigmodel.cn" in url or "zhipuai" in url:
            body["thinking"] = {"type": "disabled"}
        # Qwen3.8 thinks by default, and those tokens count against max_tokens.
        # A session title only has 200, so the call ends before message.content exists.
        spec = get_chat_model(self.settings.model)
        if spec is not None and spec.id == "qwen":
            body["chat_template_kwargs"] = {"enable_thinking": False}
        return {"extra_body": body} if body else {}

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
            **self._request_extra(),
        )
        text = resp.choices[0].message.content or ""
        parsed = parse_json_loose(text)
        if not isinstance(parsed, dict):
            raise TypeError("model did not return a JSON object")
        self.model_outputs.append(parsed)
        from ..orchestrator.status import note_model_output
        note_model_output(parsed)
        return parsed

    def stream_text(self, *, system, messages, temperature=0.7, max_tokens=300):
        stream = self._client.chat.completions.create(
            model=self.settings.model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            messages=[{"role": "system", "content": system}, *messages],
            timeout=self.settings.timeout,
            **self._request_extra(),
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
            **self._request_extra(),
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
