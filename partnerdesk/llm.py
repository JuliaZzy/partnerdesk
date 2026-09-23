"""The one seam between the agent and a model.

Two operations are all the PO agent needs: a structured JSON completion (extract, resolve,
classify) and a streamed text completion (the reply). Everything else — prompts, merging,
validation — lives in the modules that call this, so a test can swap in `FakeLLM` and the
app can point at OpenAI, Ollama or GLM by changing three env vars.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from .config import LLMSettings, llm_settings, model_timeout, resolve_model_id

Message = dict[str, Any]

_FENCE = re.compile(r"^```(?:json)?\s*([\s\S]*?)\s*```$", re.IGNORECASE)


def parse_json_loose(text: str) -> Any:
    """Parse a model's JSON answer, tolerating a ```json fence some providers add."""
    trimmed = (text or "").strip()
    m = _FENCE.match(trimmed)
    return json.loads(m.group(1) if m else trimmed)


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


@dataclass
class FakeLLM:
    """Scripted model for tests. `json_replies` are consumed in order; `text_reply` streams
    word by word. Every call is recorded so a test can assert on the prompt it received."""

    json_replies: list[dict[str, Any]] = field(default_factory=list)
    text_reply: str = "Got it."
    calls: list[dict[str, Any]] = field(default_factory=list)
    fail_json: bool = False

    def complete_json(self, *, system, messages, schema=None, temperature=0.0, max_tokens=900):
        self.calls.append({"kind": "json", "system": system, "messages": messages, "schema": schema})
        if self.fail_json:
            raise RuntimeError("model unavailable")
        if not self.json_replies:
            raise RuntimeError("FakeLLM: no scripted JSON reply left")
        return self.json_replies.pop(0)

    def stream_text(self, *, system, messages, temperature=0.7, max_tokens=300):
        self.calls.append({"kind": "text", "system": system, "messages": messages})
        for i, word in enumerate(self.text_reply.split(" ")):
            yield (" " if i else "") + word
