"""CLI: tell a brand what their agent is doing, and what is theirs to do.

    <work-state JSON on stdin> | python -m email_agent.tasks.compose_work_digest

Prints {"ok": true, "result": {"headline": "...", "followed": "...", "attention": "...",
"done": "..."}} on the last stdout line. Any line may be an empty string when its bucket is
empty — the caller renders only what is non-empty.

This replaces the topic-count digest (`compose_digest.py`, still used for the per-partner
brief). The difference is the whole point of the module: a brand does not need to be told
what kind of email they received — they can see their own inbox, and a classification is how
the SYSTEM decides what to do, not news to a human. What they cannot see is where each piece
of work stands and which part is still theirs.

So the input is not counts by category. It is work grouped by who is blocked:

  followed  — the agent has it, the distributor owes the next move
  attention — the brand has to act
  done      — closed out

Two hard rules, because breaking either turns this from a status report into a liar:

  1. Never claim the agent is handling something whose item says `handled: false`. Today only
     purchase orders have an agent that acts; everything else is classified and then waits
     for a human. Those items go in `attention` and must be described as needing the brand,
     never as "being taken care of".
  2. Never invent or alter a number, an order id, or a partner name. They are computed in SQL
     and passed in. Restate them; do not do arithmetic on them.
"""

from __future__ import annotations

import json
import re
import sys

from contract_ai._env import load_python_dotenv

load_python_dotenv()

from .._io import emit_err, emit_ok, read_stdin_json  # noqa: E402
from ..llm_client import (  # noqa: E402
    email_agent_config,
    extra_params,
    extract_text,
    parse_json_loose,
    response_format,
)

WORK_DIGEST_SCHEMA = {
    "name": "agent_work_digest",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "headline": {"type": "string"},
            "followed": {"type": "string"},
            "attention": {"type": "string"},
            "done": {"type": "string"},
        },
        "required": ["headline", "followed", "attention", "done"],
    },
}

SYSTEM_PROMPT = """You write a short status report for a brand about the work their AI teammate, Summit, is doing on their distributor relationships. The brand skims this to know what is moving and what still needs them.

You are given work already grouped into three buckets. Write one line for each non-empty bucket, plus a one-sentence headline.

- headline: what is happening overall, in one sentence. Lead with what needs the brand if anything does.
- followed: what Summit is doing. NAME SUMMIT AS THE SUBJECT — "Summit is chasing…", "Summit asked…". This line is the reader learning that someone is already carrying this work for them, so it must read as an action Summit is taking, never as a label or a fragment. Never open with a quoted name or a colon.
- attention: what the brand has to do, and nothing else.
- done: what you completed. Past tense, brief.

EACH LINE COVERS ITS OWN BUCKET ONLY. This is the rule most easily broken and the one that makes the report unreadable when it is. Do not mention the order you are handling in the `attention` line. Do not mention the messages needing the brand in the `followed` line. A fact appears in exactly one line, never two.

Return "" for any bucket that has no items. Do not write a line saying a bucket is empty.

Rules:
- SUMMARISE, do not enumerate. You are given the underlying items so you know what they are; the reader wants one sentence that characterises them, not the list pasted back. With more than two items, say how many and what they are broadly.
- HARD LIMIT: 15 words per line. These lines are read at a glance in a header strip, not studied. If you cannot fit the detail, drop the detail — the count and the shape of the work matter, the specifics do not.
- Lead with the number when there is more than one ("Four messages need you: ..."). The reader wants the size of the problem before its contents.
- Do not name the brand in the lines. The reader is the brand; telling them their own name costs words and says nothing.
- Restate partner names and order ids exactly as given, and only where they help. Never invent, total, or recalculate a number. Never put a bare count next to an item's text.
- An item marked "nobody is handling this yet" must be described as needing the brand. NEVER say Summit is working on it, looking at it, or taking care of it. This is the difference between a status report and a lie.
- Call the agent "Summit" by name, never "I" or "we". The reader has no idea who "I" is; the whole point of the line is that a named teammate is on it.
- No greeting, no sign-off, no markdown, no bullet lists, no numbered lists.
- Write in the language named by `locale` ('zh' = Chinese, 'en' = English).

Worked example. Given one handled order waiting on a distributor called Sunrise Trading, and five messages nobody handles yet (a stock question, a pricing question, sales data, a remittance query, a check-in):

{"headline": "Five messages need you; Summit is running one order with Sunrise Trading.",
 "followed": "Summit asked Sunrise Trading for the missing order details and is waiting on them.",
 "attention": "Five messages need you: stock, pricing, remittance, sales data, a check-in.",
 "done": ""}

Notice what the example does NOT do: it does not repeat all five subject lines; it does not claim I am handling any of them; no line runs past 15 words; and — most importantly — the order appears ONLY in `followed` and the five messages appear ONLY in `attention`. Neither line mentions the other's work.

Output only valid JSON matching the schema."""

# GLM/Ollama ignore json_schema response_format, so spell the shape out in the prompt.
GLM_JSON_SHAPE_INSTRUCTIONS = """
Respond with ONLY a single raw JSON object — no markdown, no code fences. Exactly:
{"headline": string, "followed": string, "attention": string, "done": string}"""


def _bucket_lines(items: list, unhandled_note: bool = False) -> list[str]:
    """One factual line per item, with the not-handled marker where it applies."""
    out = []
    for it in items:
        fact = (it.get("fact") or "").strip()
        if not fact:
            continue
        if unhandled_note and it.get("handled") is False:
            fact = f"{fact}  [nobody is handling this yet — the brand has to]"
        out.append(f"- {fact}")
    return out


def build_user_prompt(ctx: dict) -> str:
    followed = ctx.get("followed") or []
    attention = ctx.get("attention") or []
    done = ctx.get("done") or []

    # Counts go on their own labelled line, never in a parenthesis touching the item list.
    # Measured on the local model: a header like "NEEDS THE BRAND (5):" got the 5 glued onto
    # the first item's text ("Distributor check-in 5"), because the digit sat adjacent to it.
    lines = [
        f"Locale: {ctx.get('locale') or 'en'}",
        f"Brand: {ctx['brandName']}" if ctx.get("brandName") else None,
        "",
        "How many are in each bucket:",
        f"  handling, waiting on the distributor = {len(followed)}",
        f"  needs the brand = {len(attention)}",
        f"  completed = {len(done)}",
        "",
        "SUMMIT IS HANDLING — waiting on the distributor:",
    ]
    lines += _bucket_lines(followed) or ["- (none)"]
    lines += ["", "NEEDS THE BRAND:"]
    lines += _bucket_lines(attention, unhandled_note=True) or ["- (none)"]
    lines += ["", "COMPLETED:"]
    lines += _bucket_lines(done) or ["- (none)"]

    return "\n".join(line for line in lines if line is not None)


MAX_WORDS_PER_LINE = 15

# Capitalised words that are never a proper noun worth checking — sentence openers the model
# legitimately produces, plus the agent's own name.
_NAME_STOPLIST = {
    "summit", "i", "a", "an", "the", "running", "waiting", "four", "five", "six", "seven",
    "eight", "nine", "ten", "one", "two", "three", "no", "nothing", "distributor",
    "distributors", "messages", "message", "order", "orders", "sales", "stock", "pricing",
    "payment", "product", "products", "remittance", "confirm", "confirmation", "needs",
    "need", "reply", "replies", "brand", "your", "their", "with", "on", "of", "and", "to",
}


def _invented_names(text: str, source: str) -> list[str]:
    """Capitalised words in `text` that do not appear anywhere in `source`.

    The guard exists because prompt rules do not hold. Measured on the local model: given
    "Zhao Zhuoying / Sunrise Trading", it wrote "Running one order with Zhangming Trading" —
    a distributor that does not exist, in a line a brand would read as fact. A fabricated
    partner name is not a wording problem, so it is caught in code rather than asked for
    politely in the prompt.

    Deliberately crude: it only asks whether a capitalised token was present in the input at
    all. That catches an invented name while accepting any rephrasing of real ones, which is
    the trade a summariser needs.
    """
    src = source.lower()
    bad = []
    # Skip the first word of each sentence: it is capitalised by grammar, not because it is a
    # name, and checking it flags ordinary verbs ("Escalated…", "Confirmed…"). A two-word
    # invention is still caught by its second word ("Zhangming *Trading*"), and the risk is
    # asymmetric — a false positive discards a correct brief every single time it fires.
    body = re.sub(r"(?:^|(?<=[.!?:;])\s+)[A-Z][A-Za-z'’\-]*", " ", text)
    for raw in re.findall(r"\b[A-Z][A-Za-z'’\-]+", body):
        # Keep only what precedes any apostrophe, which handles possessives AND contractions
        # in one step. Both break a naive substring test and both occur constantly:
        # "Zhuoying's reply" is not a substring of "...Zhao Zhuoying for...". An earlier
        # version stripped only possessives and rejected its own correct output.
        word = re.split(r"['’]", raw.lower())[0]
        if word in _NAME_STOPLIST or len(word) < 3:
            continue
        if word not in src:
            bad.append(raw)
    return bad


_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}


def _bad_numbers(text: str, allowed: set[int]) -> list[str]:
    """Numbers in `text` that are not a count the line is entitled to state.

    Measured on the local model: with four items in `attention`, it wrote "five distributor
    inquiries await response". A wrong count is worse than a vague one — the operator plans
    around it — and unlike wording, the true value is right here, so it is checked rather
    than requested.

    `allowed` is that bucket's own count plus the counts a line may legitimately reference.
    A line with no numbers at all is always fine.
    """
    found = []
    for tok in re.findall(r"\b\d+\b", text):
        if int(tok) not in allowed:
            found.append(tok)
    for word, value in _NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", text, re.IGNORECASE) and value not in allowed:
            found.append(word)
    return found


def compose_work_digest(ctx: dict) -> dict:
    client, model, provider = email_agent_config("compose")
    system = SYSTEM_PROMPT if provider == "openai" else f"{SYSTEM_PROMPT}\n{GLM_JSON_SHAPE_INSTRUCTIONS}"

    # gpt-5 is a reasoning model and only allows the default temperature — omitted here, same
    # as the sibling compose tasks.
    resp = client.with_options(max_retries=1).chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": build_user_prompt(ctx)},
        ],
        **response_format(provider, WORK_DIGEST_SCHEMA),
        **extra_params(provider),
    )
    parsed = parse_json_loose(extract_text(resp))
    if not isinstance(parsed, dict):
        raise ValueError("Model did not return a JSON object")

    def line(key: str) -> str:
        v = parsed.get(key)
        return v.strip() if isinstance(v, str) else ""

    headline = line("headline")
    if not headline:
        raise ValueError("Model returned an empty headline")

    # A bucket with items but no line is a silent omission — the operator would never know
    # something was dropped. Fail loudly instead; the caller keeps the previous digest.
    for key in ("followed", "attention", "done"):
        if (ctx.get(key) or []) and not line(key):
            raise ValueError(f"Model omitted the '{key}' line while it had items")

    # Everything the model was actually given. Any proper noun outside this was invented.
    source = build_user_prompt(ctx)
    counts = {k: len(ctx.get(k) or []) for k in ("followed", "attention", "done")}
    for key in ("headline", "followed", "attention", "done"):
        text = line(key)
        if not text:
            continue

        # HARD: a count the line is not entitled to state. A bucket line may cite its own
        # count; the headline spans all three, so it may cite any of them. 0 and 1 are always
        # allowed — "one order", "a single message" are ordinary phrasing, not claims.
        allowed = {0, 1} | (
            set(counts.values()) if key == "headline" else {counts.get(key, 0)}
        )
        wrong = _bad_numbers(text, allowed)
        if wrong:
            raise ValueError(f"Model stated a wrong count in '{key}': {', '.join(wrong)} (real: {counts})")
        # HARD: a name the model made up. Raising means the caller keeps the previous brief,
        # which is strictly better than showing a brand a distributor that does not exist.
        invented = _invented_names(text, source)
        if invented:
            raise ValueError(f"Model invented a name in '{key}': {', '.join(invented)}")
        # SOFT: over-long lines are ugly in a header strip, not dangerous. Warn on stderr and
        # keep the line — losing a correct brief over its length would be the worse trade.
        n = len(text.split())
        if n > MAX_WORDS_PER_LINE:
            print(f"[compose_work_digest] '{key}' ran to {n} words (limit {MAX_WORDS_PER_LINE})", file=sys.stderr)

    return {
        "headline": headline,
        "followed": line("followed"),
        "attention": line("attention"),
        "done": line("done"),
    }


def main() -> int:
    try:
        ctx = read_stdin_json()
    except Exception as err:  # noqa: BLE001
        emit_err(f"Invalid stdin JSON: {err}")
        return 1
    if not isinstance(ctx, dict):
        emit_err("Expected a work-state object on stdin.")
        return 1
    try:
        emit_ok(compose_work_digest(ctx))
    except Exception as err:  # noqa: BLE001
        emit_err(str(err))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
