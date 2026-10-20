"""Call 2 of a turn: the streamed human reply.

Runs AFTER the code-check and is TOLD the gate outcome (gaps / advisories / stage), so the
streamed text is accurate. The draft table is rendered by the UI from code — the reply
only talks, it never prints a table. The voice and length rules live in po/reply.md.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from ..llm import LLM
from ..memory import prompt_turns
from .extract import context_block
from .prompt import REPLY, fill


@dataclass
class ReplyInput:
    tone: str
    language: str
    user_message: str
    stage: str  # gathering | confirm | submitted
    draft_summary: str
    resolutions: list[tuple[str, str]] = field(default_factory=list)  # (said, got)
    gaps: list[str] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)
    layer_notes: list[str] = field(default_factory=list)
    image_count: int = 0
    submitted: dict | None = None  # {po_number, total, currency}
    operating_context: str | None = None
    contract_discount: str | None = None  # set on the turn the contract rate was applied
    history: list[dict] = field(default_factory=list)
    folded_through: int = 0


def _stage_block(i: ReplyInput) -> str:
    if i.stage == "gathering":
        return (
            f"Still to sort out (weave in naturally, don't recite): {' | '.join(i.gaps)}" if i.gaps
            else "Ask what they'd like to order."
        )
    if i.stage == "confirm":
        bits = [f"Mention: {' | '.join(i.advisories)}"] if i.advisories else []
        bits.append(
            "It is complete and passes every rule. Confirm places a real order, so before they press it walk them through "
            "what is on it, from the order above: each product with its case count, the discount and where it comes from "
            "(their contract, or what they asked for, or none), payment terms, incoterms, shipping, ETA and the total. "
            "Then ask them to check every line and press Confirm only once it all matches what they want. "
            "Never say it looks fine or tell them to just confirm — checking is theirs to do, not yours to wave through."
        )
        return "\n".join(bits)
    s = i.submitted or {}
    bits = [f"Placed as {s.get('po_number')} ({s.get('currency')} {s.get('total')})." if s else "Placed."]
    if i.advisories:
        bits.append(f"Needs brand approval because: {' | '.join(i.advisories)}")
    bits.append("Confirm it's done and what's next.")
    return "\n".join(bits)


def build_reply_prompt(i: ReplyInput) -> str:
    length = (
        "Up to about six short lines — this is the last thing they read before committing money."
        if i.stage == "confirm" else "1–3 short sentences."
    )
    image_note = ""
    if i.image_count:
        n = "an image" if i.image_count == 1 else f"{i.image_count} images"
        it = "it" if i.image_count == 1 else "them"
        image_note = (
            f"They also attached {n}, and the order below is what was read off {it}. "
            "Say briefly that you read it and ask them to check the draft — reading a photo can go wrong, so make correcting it feel easy."
        )
    resolutions = ""
    if i.resolutions:
        lines = [f'They said "{said}" → you carry "{got}".' for said, got in i.resolutions]
        lines.append("If that isn't literally what they asked for, acknowledge it and offer it as the closest match rather than silently swapping.")
        resolutions = "\n".join(lines)
    contract = ""
    if i.contract_discount:
        contract = (
            f"This turn you put their contract rate on the order: {i.contract_discount}. Tell them so in this reply — "
            "a discount is money and must never be applied silently. State it as the agreed term from their contract, "
            "never as something they could request, apply for, or negotiate."
        )
    layer = ""
    if i.stage != "submitted" and i.layer_notes:
        layer = f"Optionally mention (offer, don't insist — the order is fine as-is): {' | '.join(i.layer_notes)}"
    text = fill(
        REPLY,
        tone=i.tone,
        language=i.language or "the customer language",
        length_rule=length,
        context_block=context_block(i.operating_context),
        customer_said=f'The customer just said: "{i.user_message}"' if i.user_message else "",
        image_note=image_note,
        resolutions=resolutions,
        draft_summary=i.draft_summary or "nothing yet",
        contract_discount=contract,
        stage_block=_stage_block(i),
        layer_notes=layer,
    )
    return "\n".join(line for line in text.splitlines() if line)


def stream_reply(llm: LLM, i: ReplyInput) -> Iterator[str]:
    # The user turn is required, not decorative: with a system-only array a local Llama
    # chat template completes the missing turn itself and emits a literal "assistant\n\n"
    # as the first tokens of the reply (3/3 on llama3.2:3b).
    prior = [
        {"role": turn.get("role"), "content": str(turn.get("content") or "")}
        for turn in prompt_turns(i.history, i.folded_through)
        if turn.get("role") in ("user", "assistant") and str(turn.get("content") or "").strip()
    ]
    prior.append({"role": "user", "content": i.user_message.strip() or "(they sent an image with no caption)"})
    yield from llm.stream_text(
        system=build_reply_prompt(i),
        messages=prior,
        temperature=0.7, max_tokens=450 if i.stage == "confirm" else 300,
    )
