from backend.config import CHAT_MODELS, LLMSettings, chat_models, resolve_model_id
from backend.llm import OpenAICompatLLM


class _Capture:
    def __init__(self, content: str):
        self.kwargs: dict = {}
        self._content = content

    def create(self, **kwargs):
        self.kwargs = kwargs
        message = type("M", (), {"content": self._content, "tool_calls": None})()
        choice = type("C", (), {"message": message, "delta": message})()
        return type("R", (), {"choices": [choice]})()


def _llm(model: str, capture: _Capture) -> OpenAICompatLLM:
    settings = LLMSettings(
        base_url="https://models.sjtu.edu.cn/api/v1", api_key="x", model=model, json_mode="object",
    )
    client = type("Client", (), {"chat": type("Chat", (), {"completions": capture})()})()
    return OpenAICompatLLM(settings=settings, _client=client)


def test_campus_catalog_is_the_four_school_models():
    assert [m.id for m in CHAT_MODELS] == ["deepseek-chat", "deepseek-reasoner", "minimax", "qwen"]


def test_aliases_map_to_canonical_ids():
    assert resolve_model_id("minimax-m2.7") == "minimax"
    assert resolve_model_id("qwen3.8-27b") == "qwen"
    assert resolve_model_id("") == "deepseek-chat"
    assert resolve_model_id("gpt-4.1") == "gpt-4.1"


def test_chat_models_are_the_config_rows_not_the_env_model(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "glm-4.6")
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    assert [m.id for m in chat_models()] == ["deepseek-chat", "deepseek-reasoner", "minimax", "qwen"]


def test_with_model_uses_the_config_url_and_call_name():
    from backend.llm_config import CAMPUS_BASE_URL

    held = object()
    settings = LLMSettings(
        base_url="https://openrouter.ai/api/v1", api_key="x", model="openrouter/auto", json_mode="object",
    )
    llm = OpenAICompatLLM(settings=settings, _client=held)
    other = llm.with_model("qwen3.8-27b")
    assert other.settings.base_url == CAMPUS_BASE_URL
    assert other.settings.model == "qwen"
    assert other.settings.timeout == 60.0
    assert other._client is not held
    assert llm.settings.model == "openrouter/auto"

    campus = OpenAICompatLLM(
        settings=LLMSettings(base_url=CAMPUS_BASE_URL, api_key="x", model="deepseek-chat", json_mode="object"),
        _client=held,
    )
    thinking = campus.with_model("deepseek-reasoner")
    assert thinking.settings.base_url == CAMPUS_BASE_URL
    assert thinking.settings.model == "deepseek-reasoner"
    assert thinking.settings.timeout == 180.0
    assert thinking._client is held


def test_qwen_disables_default_thinking_so_a_short_json_call_can_finish():
    capture = _Capture('{"title": "鱼油", "tags": []}')
    llm = _llm("qwen", capture)
    out = llm.complete_json(
        system="Return JSON.", messages=[{"role": "user", "content": "鱼油"}], max_tokens=200,
    )
    assert out["title"] == "鱼油"
    assert llm.model_outputs == [{"title": "鱼油", "tags": []}]
    assert capture.kwargs["model"] == "qwen"
    assert capture.kwargs["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert capture.kwargs["max_tokens"] == 200


def test_dropdown_sends_the_campus_call_name():
    pairs = (
        ("deepseek-chat", "deepseek-chat"),
        ("deepseek-reasoner", "deepseek-reasoner"),
        ("minimax", "minimax"),
        ("minimax-m2.7", "minimax"),
        ("qwen", "qwen"),
        ("qwen3.8-27b", "qwen"),
    )
    for chosen, sent in pairs:
        capture = _Capture('{"ok": true}')
        _llm("deepseek-chat", capture).with_model(chosen).complete_json(
            system="Return JSON.", messages=[{"role": "user", "content": "hi"}],
        )
        assert capture.kwargs["model"] == sent


def test_qwen_alias_also_disables_thinking():
    capture = _Capture('{"title": "鱼油"}')
    _llm("deepseek-chat", capture).with_model("qwen3.8-27b").complete_json(
        system="Return JSON.", messages=[{"role": "user", "content": "鱼油"}],
    )
    assert capture.kwargs["model"] == "qwen"
    assert capture.kwargs["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_each_request_keeps_its_own_model_outputs():
    from backend.deps import llm_for

    capture = _Capture('{"ok": true}')
    shared = _llm("deepseek-chat", capture)
    first = llm_for(shared, "deepseek-reasoner")
    second = llm_for(shared, None)
    assert first.model_outputs is not second.model_outputs
    assert first.model_outputs is not shared.model_outputs
    first.complete_json(system="Return JSON.", messages=[{"role": "user", "content": "hi"}])
    second.complete_json(system="Return JSON.", messages=[{"role": "user", "content": "hi"}])
    assert first.model_outputs == [{"ok": True}]
    assert second.model_outputs == [{"ok": True}]
    assert shared.model_outputs == []
    assert first._client is shared._client
    assert second._client is shared._client


def test_other_models_do_not_send_the_qwen_thinking_switch():
    capture = _Capture('{"ok": true}')
    _llm("deepseek-chat", capture).complete_json(
        system="Return JSON.", messages=[{"role": "user", "content": "hi"}],
    )
    assert "extra_body" not in capture.kwargs
