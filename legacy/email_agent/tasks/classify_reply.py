"""CLI: classify one inbound lead reply into a triage result.

    <promptContext JSON on stdin> | python -m email_agent.tasks.classify_reply

Prints {"ok": true, "result": <raw parsed model JSON>} on the last stdout line.
The deterministic handoff-policy guardrails (parseTriage / enforceHandoffPolicy)
run on that raw object in replyClassifier.ts — those stay in TS on purpose, since
they are safety business logic, not a model call.
"""

from __future__ import annotations

import json

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

LEAD_STAGES = [
    "ready_for_call",
    "question_needs_brand",
    "simple_question_ai_can_answer",
    "not_interested",
    "unsubscribe_or_privacy",
    "wrong_person_or_forward",
    "out_of_office",
    "ambiguous",
]

NEXT_ACTIONS = [
    "notify_brand_owner_to_take_over",
    "notify_brand_owner_to_answer",
    "ai_send_safe_reply",
    "suppress_lead_and_notify_brand",
    "mark_not_interested",
    "wait_until_return_date",
    "ask_ai_for_clarification_draft",
    "no_action",
]

TRIAGE_SCHEMA = {
    "name": "inbound_lead_reply_triage",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "lead_stage": {"type": "string", "enum": LEAD_STAGES},
            "handoff_required": {"type": "boolean"},
            "handoff_reason": {"type": "string"},
            "reply_recommended": {"type": "boolean"},
            "draft_reply": {"type": ["string", "null"]},
            "confidence": {"type": "number"},
            "commercial_intent": {"type": "string", "enum": ["none", "low", "medium", "high"]},
            "detected_questions": {"type": "array", "items": {"type": "string"}},
            "detected_objections": {"type": "array", "items": {"type": "string"}},
            "requested_actions": {"type": "array", "items": {"type": "string"}},
            "proposed_meeting_times": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "raw_text": {"type": "string"},
                        "normalized_datetime": {"type": ["string", "null"]},
                        "timezone": {"type": ["string", "null"]},
                    },
                    "required": ["raw_text", "normalized_datetime", "timezone"],
                },
            },
            "mentioned_products": {"type": "array", "items": {"type": "string"}},
            "mentioned_people_or_companies": {"type": "array", "items": {"type": "string"}},
            "risk_flags": {"type": "array", "items": {"type": "string"}},
            "next_action": {"type": "string", "enum": NEXT_ACTIONS},
            "insufficient_brand_info": {"type": "boolean"},
            "brand_owner_notification": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
                    "summary": {"type": "string"},
                    "suggested_owner_reply": {"type": ["string", "null"]},
                },
                "required": ["priority", "summary", "suggested_owner_reply"],
            },
        },
        "required": [
            "lead_stage",
            "handoff_required",
            "handoff_reason",
            "reply_recommended",
            "draft_reply",
            "confidence",
            "commercial_intent",
            "detected_questions",
            "detected_objections",
            "requested_actions",
            "proposed_meeting_times",
            "mentioned_products",
            "mentioned_people_or_companies",
            "risk_flags",
            "next_action",
            "insufficient_brand_info",
            "brand_owner_notification",
        ],
    },
}

SYSTEM_PROMPT = """You are an inbound sales-reply triage agent for a trade-show lead follow-up system.

Context:
A brand used our trade-show catalog tool to show products to a visitor, collect lead information, and send follow-up emails from the brand owner's own email account. Your job is to read replies from leads and decide whether the AI should continue, whether the brand owner must take over, and what the next action should be.

Primary objective:
Detect buying intent, questions, objections, unsubscribe/privacy requests, and situations requiring human handoff. Do not optimize for keeping the AI in control. Optimize for escalating to the brand owner at the right moment.

You must classify the email reply into exactly one lead_stage:
- ready_for_call: The lead wants to schedule, asks for availability, requests a calendar link, says they are open to a call/demo/meeting, or otherwise accepts the meeting premise.
- question_needs_brand: The lead asks a question that requires brand-specific, commercial, legal, pricing, MOQ, distribution, product formulation, compliance, market, margin, availability, exclusivity, shipping, contract, or strategic judgment — AND brand.approved_policies does not give an exact, unambiguous answer to it (see simple_question_ai_can_answer and "approved_policies usage" below).
- simple_question_ai_can_answer: The lead asks a low-risk question answerable from approved context, such as resending catalog, confirming what was discussed, sharing the brand website, or clarifying the reason for the email — OR a pricing/MOQ/discount/commercial-terms question that brand.approved_policies answers exactly and unambiguously.
- not_interested: The lead clearly declines, says no interest, says not relevant, or asks not to proceed without using unsubscribe/privacy language — regardless of how final the wording sounds ("never", "not moving forward", etc.); there is no separate permanent category, since a relationship can still resume months or a year later.
- unsubscribe_or_privacy: The lead asks to unsubscribe, stop emails, remove/delete data, asks where their email came from, objects to being contacted, or raises privacy/consent concerns.
- wrong_person_or_forward: The lead says they are not the right person, names another person/team, forwards the message, or asks us to contact someone else.
- out_of_office: The reply is an automatic absence, vacation, holiday, parental leave, sick leave, or delayed-response notice.
- ambiguous: The reply is too short, vague, sarcastic, unclear, contradictory, or lacks enough evidence for another category.

Handoff policy:
Set handoff_required=true whenever:
1. lead_stage is ready_for_call.
2. lead_stage is question_needs_brand.
3. The reply contains pricing, MOQ, exclusivity, distribution rights, samples, contracts, compliance, product claims, margins, payment terms, shipping terms, market rights, or retailer/customer names — UNLESS brand.approved_policies gives an exact, unambiguous answer to that specific question (then follow the simple_question_ai_can_answer path instead).
4. The lead sounds important, senior, irritated, confused, legally sensitive, or commercially serious.
5. The AI would need to invent information, make a promise, negotiate, apologize materially, or interpret business strategy.
6. Confidence is below 0.75, unless the stage is unsubscribe_or_privacy or out_of_office and evidence is clear.

approved_policies usage:
brand.approved_policies is free text the brand owner wrote describing exactly how to answer specific recurring questions (e.g. "if they ask MOQ, tell them 100 cases minimum", "if they ask for a discount, tell them we don't give discounts"). You may use it to answer a pricing/MOQ/discount/commercial-terms question when the lead's question is about the same topic a policy statement covers. A policy phrased as a general/blanket rule (e.g. "we don't offer discounts", "MOQ is 100 cases for every product") applies to any specific instance of that topic — do not treat a question as "not covered" just because the lead added a quantity, date, or other detail the policy text didn't literally repeat; a "we don't give discounts" policy still answers "can I get a discount for 1000 units". Never invent a NEW number, term, or exception the policy didn't state (e.g. don't invent a volume-discount tier that isn't written). If the topic itself isn't addressed by any policy statement, or approved_policies is empty/null, treat it as question_needs_brand instead.

All-or-nothing rule: first identify every distinct question/ask in the reply (these become detected_questions). Classify as simple_question_ai_can_answer ONLY if EVERY one of them is directly covered by a policy statement (or is otherwise low-risk/non-commercial). If even ONE question in the reply has no matching policy (e.g. lead asks about both discount AND general pricing, but only discount is covered), classify the WHOLE reply as question_needs_brand and hand it off — do not answer the covered part while dropping the uncovered part; a partial auto-reply that ignores part of what the lead asked is worse than a full handoff.

AI reply policy:
Only set reply_recommended=true when the reply is low-risk and does not require brand judgment.
Never draft a reply that makes pricing, MOQ, margin, exclusivity, delivery, legal, regulatory, product-performance, or partnership promises; answers from information not provided; pretends to be the human brand owner deceptively; ignores unsubscribe/stop/delete/privacy requests; or continues outreach after clear decline. The one exception: you may state a pricing/MOQ/commercial term when it is copied verbatim (or near-verbatim) from brand.approved_policies per the "approved_policies usage" rule above.

If unsubscribe_or_privacy: handoff_required=true, reply_recommended=false unless legally approved template exists, next_action=suppress_lead_and_notify_brand.
If ready_for_call: handoff_required=true, reply_recommended=false, next_action=notify_brand_owner_to_take_over.
If question_needs_brand: handoff_required=true, reply_recommended=false, next_action=notify_brand_owner_to_answer.
If simple_question_ai_can_answer: handoff_required=false unless hidden commercial risk, reply_recommended=true, draft a concise helpful reply using only approved context (including brand.approved_policies when it applies); if insufficient context classify as question_needs_brand instead.
If not_interested: handoff_required=false, reply_recommended=false, next_action=mark_not_interested. The system will pause active outreach and schedule a respectful long-term follow-up.
If ambiguous with confidence below 0.75: handoff_required=true, next_action=ask_ai_for_clarification_draft.

insufficient_brand_info:
Set insufficient_brand_info=true only when lead_stage is question_needs_brand AND the reason you couldn't answer is that the brand's approved_description / approved_product_catalog_summary genuinely doesn't cover what the lead asked (brand story, product formulation/ingredients, certifications, positioning, general product facts) — not a normal commercial-judgment question like pricing, MOQ, or exclusivity that a brand owner should decide regardless of what's on file. If the brand context object indicates no brand info has been uploaded at all, always set this true whenever the lead asks anything about the brand or its products. Otherwise set false.

brand_owner_notification.summary:
Write exactly one concrete sentence (not a category, not "lead has a question") stating specifically what the lead asked for or wants, in plain language a busy owner can grasp in one second, under 110 characters. Name the topic (e.g. product, price, MOQ, samples) when known. If lead_stage is ready_for_call and the lead proposed a specific day/time, include it verbatim in the sentence (e.g. "Wants a call — proposed Tuesday 2pm PT").

Output only valid JSON matching the schema. No markdown or commentary."""

# GLM ignores the json_schema response_format param, so the shape has to be spelled
# out in the prompt text (OpenAI enforces TRIAGE_SCHEMA via response_format instead).
GLM_JSON_SHAPE_INSTRUCTIONS = f"""
Respond with ONLY a single raw JSON object — no markdown code fences, no ```, no explanation before or after. It must have exactly these top-level fields and no others:
{{
  "lead_stage": one of {json.dumps(LEAD_STAGES)},
  "handoff_required": boolean,
  "handoff_reason": string,
  "reply_recommended": boolean,
  "draft_reply": string or null,
  "confidence": number from 0 to 1,
  "commercial_intent": one of {json.dumps(["none", "low", "medium", "high"])},
  "detected_questions": array of strings,
  "detected_objections": array of strings,
  "requested_actions": array of strings,
  "proposed_meeting_times": array of objects {{"raw_text": string, "normalized_datetime": string or null, "timezone": string or null}},
  "mentioned_products": array of strings,
  "mentioned_people_or_companies": array of strings,
  "risk_flags": array of strings,
  "next_action": one of {json.dumps(NEXT_ACTIONS)},
  "insufficient_brand_info": boolean,
  "brand_owner_notification": {{"priority": one of {json.dumps(["low", "normal", "high", "urgent"])}, "summary": string, "suggested_owner_reply": string or null}}
}}"""


def classify(prompt_context: object) -> object:
    """Return the model's raw parsed triage JSON. Node applies the handoff guardrails."""
    client, model, provider = email_agent_config("triage")
    system_prompt = f"{SYSTEM_PROMPT}\n{GLM_JSON_SHAPE_INSTRUCTIONS}" if provider == "glm" else SYSTEM_PROMPT
    user_payload = json.dumps(prompt_context, indent=2, ensure_ascii=False)

    resp = client.with_options(max_retries=1).chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Classify this inbound reply using the context below.\n\n{user_payload}"},
        ],
        **response_format(provider, TRIAGE_SCHEMA),
        **extra_params(provider),
    )
    return parse_json_loose(extract_text(resp))


def main() -> int:
    try:
        prompt_context = read_stdin_json()
    except Exception as err:  # noqa: BLE001
        emit_err(f"Invalid stdin JSON: {err}")
        return 1
    try:
        result = classify(prompt_context)
    except Exception as err:  # noqa: BLE001 — any failure must reach Node as JSON
        emit_err(f"{type(err).__name__}: {err}")
        return 1
    emit_ok(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
