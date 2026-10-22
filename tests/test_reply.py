"""What the reply model is told. The draft summary and the stage instructions are the only
things standing between the model and a wrong number or a waved-through confirm."""

from __future__ import annotations

from datetime import date

from backend.po.check import run_code_check
from backend.po.draft import build_draft_summary
from backend.po.reply import ReplyInput, build_reply_prompt
from backend.po.slots import Discount, LineItemSlot, PoSlots
from backend.schemas import AgentSettings


def test_summary_spells_out_cases_provenance_and_money(catalog):
    slots = PoSlots(
        line_items=[LineItemSlot(reference="FO-100", cases=20)],
        discount=Discount(kind="percent", value=3, source="contract", note="1st container of the contract period from 2026-01-01 §5.01(b)"),
    )
    check = run_code_check(slots=slots, catalog=catalog, rules=[], partnership_id="ps-1", ship_to_country="US")
    s = build_draft_summary(slots, check, AgentSettings(), "USD", today=date(2026, 9, 20))
    assert "20 箱 Deep Sea Fish Oil" in s and "20×" not in s
    assert "合同折扣 3%（1st container of the contract period from 2026-01-01 §5.01(b)）" in s
    assert "小计 USD 2,000.00，折扣 USD 60.00，合计 USD 1,940.00。" in s
    assert "付款 50_50（下单时预付 50%，货到验收后再付 50% 尾款）" in s


def _input(stage: str, **over) -> ReplyInput:
    base = dict(tone="Plain.", language="Chinese", user_message="ok", stage=stage, draft_summary="20 cases of X. total USD 1,940.00")
    return ReplyInput(**{**base, **over})


def test_confirm_stage_is_a_walkthrough_not_a_nudge():
    p = build_reply_prompt(_input("confirm"))
    assert "逐项说清楚" in p and "每一款产品及其箱数" in p and "折扣以及它从哪来" in p
    assert "逐行核对" in p and "不要说看起来没问题" in p
    assert "大约六行" in p


def test_gathering_stage_stays_short_and_the_contract_rate_is_said_out_loud():
    p = build_reply_prompt(_input("gathering", gaps=["Is this order at list price, or is there an agreed discount to put on it?"]))
    assert "一到三句" in p and "一律说箱" in p
    p = build_reply_prompt(_input("gathering", contract_discount="3% off — 1st container of the contract period from 2026-01-01"))
    assert "在这条回复里告诉他们" in p and "绝不能悄悄加上" in p and "绝不能说成他们可以申请" in p
