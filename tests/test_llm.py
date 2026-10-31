"""The desk no longer sends the dropdown id. Each step names its own model."""

from backend.llm.calls import DEEPSEEK_FLASH, SCHOOL_CHAT, SCHOOL_PLAN, SCHOOL_REASONER, SCHOOL_VISION


def test_named_models():
    assert SCHOOL_CHAT == "deepseek-chat"
    assert SCHOOL_REASONER == "deepseek-reasoner"
    assert SCHOOL_PLAN == "minimax"
    assert SCHOOL_VISION == "qwen"
    assert DEEPSEEK_FLASH == "deepseek-flash"
