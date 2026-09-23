from partnerdesk.config import CHAT_MODELS, LLMSettings, chat_models, resolve_model_id
from partnerdesk.llm import OpenAICompatLLM


def test_campus_catalog_is_the_four_school_models():
    assert [m.id for m in CHAT_MODELS] == ["deepseek-chat", "deepseek-reasoner", "minimax", "qwen"]


def test_aliases_map_to_canonical_ids():
    assert resolve_model_id("minimax-m2.7") == "minimax"
    assert resolve_model_id("qwen3.8-27b") == "qwen"
    assert resolve_model_id("") == "deepseek-chat"
    assert resolve_model_id("gpt-4.1") == "gpt-4.1"


def test_chat_models_appends_unknown_llm_model(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "glm-4.6")
    ids = [m.id for m in chat_models()]
    assert ids[:4] == ["deepseek-chat", "deepseek-reasoner", "minimax", "qwen"]
    assert ids[-1] == "glm-4.6"


def test_with_model_shares_client_and_resolves_alias():
    settings = LLMSettings(
        base_url="http://example.invalid/v1", api_key="x", model="deepseek-chat", json_mode="object",
    )
    llm = OpenAICompatLLM(settings=settings)
    other = llm.with_model("qwen3.8-27b")
    assert other.settings.model == "qwen"
    assert other.settings.timeout == 60.0
    assert other._client is llm._client
    assert llm.settings.model == "deepseek-chat"

    thinking = llm.with_model("deepseek-reasoner")
    assert thinking.settings.model == "deepseek-reasoner"
    assert thinking.settings.timeout == 180.0
    assert thinking._client is llm._client
