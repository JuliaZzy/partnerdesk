"""What the reply model is told. The draft summary and the stage instructions are the only
things standing between the model and a wrong number or a waved-through confirm."""

from __future__ import annotations

from datetime import date

from partnerdesk.models import AgentSettings
from partnerdesk.po.check import run_code_check
from partnerdesk.po.draft import build_draft_summary
from partnerdesk.po.reply import ReplyInput, build_reply_prompt
from partnerdesk.po.slots import Discount, LineItemSlot, PoSlots


def test_summary_spells_out_cases_provenance_and_money(catalog):
    slots = PoSlots(
        line_items=[LineItemSlot(reference="FO-100", cases=20)],
        discount=Discount(kind="percent", value=3, source="contract", note="1st container of the contract period from 2026-01-01 §5.01(b)"),
    )
    check = run_code_check(slots=slots, catalog=catalog, rules=[], partnership_id="ps-1", ship_to_country="US")
    s = build_draft_summary(slots, check, AgentSettings(), "USD", today=date(2026, 9, 20))
    assert "20 cases of Deep Sea Fish Oil" in s and "20×" not in s
    assert "contract discount 3% (1st container of the contract period from 2026-01-01 §5.01(b))" in s
    assert "Subtotal USD 2,000.00, discount USD 60.00, total USD 1,940.00." in s
    assert "Payment 50_50 (50% prepaid with the order, 50% balance after the goods are received and inspected)" in s


def _input(stage: str, **over) -> ReplyInput:
    base = dict(tone="Plain.", language="Chinese", user_message="ok", stage=stage, draft_summary="20 cases of X. total USD 1,940.00")
    return ReplyInput(**{**base, **over})


def test_confirm_stage_is_a_walkthrough_not_a_nudge():
    p = build_reply_prompt(_input("confirm"))
    assert "walk them through" in p and "each product with its case count" in p and "the discount and where it comes from" in p
    assert "check every line" in p and "Never say it looks fine" in p
    assert "looks good" not in p
    assert "Up to about six short lines" in p


def test_gathering_stage_stays_short_and_the_contract_rate_is_said_out_loud():
    p = build_reply_prompt(_input("gathering", gaps=["Is this order at list price, or is there an agreed discount to put on it?"]))
    assert "1–3 short sentences" in p and "always say cases" in p
    p = build_reply_prompt(_input("gathering", contract_discount="3% off — 1st container of the contract period from 2026-01-01"))
    assert "Tell them so in this reply" in p and "never be applied silently" in p and "never as something they could request" in p
