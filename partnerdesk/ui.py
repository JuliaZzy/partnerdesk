"""The chat page — pure Python (Streamlit), no JavaScript.

    streamlit run partnerdesk/ui.py

Calls service.py in-process: the same event stream the HTTP API serializes, rendered
with Streamlit's chat widgets. Left: the conversation. Right: the draft the code built,
what's still missing (with one-click answers), the step ledger, and the Confirm button.
"""

from __future__ import annotations

import base64
from typing import Any

import streamlit as st

from partnerdesk import service
from partnerdesk.db import Database
from partnerdesk.llm import LLM, OpenAICompatLLM

st.set_page_config(page_title="partnerdesk", page_icon="📦", layout="wide")

STEP_ICON = {"done": "✅", "working": "🔄", "needs-you": "🟡", "pending": "⚪"}


def get_backend() -> tuple[Database, LLM]:
    """Tests inject a (db, llm) pair via session_state; the app builds the real one once."""
    if "_backend" in st.session_state:
        return st.session_state["_backend"]

    @st.cache_resource
    def _real() -> tuple[Database, LLM]:
        db = Database()
        db.seed_from_fixtures()
        return db, OpenAICompatLLM()

    return _real()


def init_state(db: Database) -> None:
    if "sid" in st.session_state:
        return
    partnership = db.documents("partnership")[0]
    session = service.new_session(db, partnership["id"])
    st.session_state.update(
        sid=session["id"], partnership=partnership, draft=None, gaps=[], steps=[], stage="gathering",
        confirm_token=None, route="", pending=None,
        turns=[{"role": "assistant", "text": f"Hi — I can place an order for {partnership['distributor_name']} with {partnership['brand_name']}. What would you like, and how many cases?"}],
    )


def to_data_url(file: Any) -> str:
    mime = getattr(file, "type", None) or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(file.getvalue()).decode()}"


def apply_done(d: dict[str, Any]) -> None:
    st.session_state.update(
        stage=d.get("stage") or st.session_state.stage, draft=d.get("draft"), gaps=d.get("gaps_detail") or [],
        steps=d.get("steps") or st.session_state.steps, confirm_token=d.get("confirm_token"),
    )


def send(db: Database, llm: LLM, text: str, images: list[str]) -> None:
    st.session_state.turns.append({"role": "user", "text": text, "images": images})
    with st.chat_message("user"):
        st.write(text)
        for u in images:
            st.image(u, width=160)
    with st.chat_message("assistant"):
        events = service.handle_message(db, llm, st.session_state.sid, text, images)
        done: dict[str, Any] = {}

        def tokens():
            for e in events:
                if e["type"] == "token":
                    yield e["text"]
                elif e["type"] == "route":
                    st.session_state.route = f"→ {e['specialist'] or 'abstain'} · {e['reason']}"
                elif e["type"] == "warning":
                    st.caption(f"⚠ {e['message']}")
                elif e["type"] == "done":
                    done.update(e)

        reply = st.write_stream(tokens)
    st.session_state.turns.append({"role": "assistant", "text": reply if isinstance(reply, str) else "".join(reply)})
    if done:
        apply_done(done)


def do_confirm(db: Database) -> None:
    r = service.confirm(db, st.session_state.sid, st.session_state.confirm_token)
    if r["state"] in ("submitted", "changed"):
        st.session_state.turns.append({"role": "assistant", "text": r["message"]})
        st.session_state.stage = "submitted" if r["state"] == "submitted" else "gathering"
        st.session_state.draft, st.session_state.steps, st.session_state.gaps = r.get("draft"), r.get("steps") or [], []
    else:
        st.session_state.turns.append({"role": "assistant", "text": f"Confirm: {r['state']}."})
    st.session_state.confirm_token = None


def render_draft(d: dict[str, Any] | None) -> None:
    if not d:
        st.caption("Nothing yet.")
        return
    cur = d["currency"]
    money = lambda n: "—" if n is None else f"{cur} {n:,.2f}"  # noqa: E731
    rows = []
    for l in d["lines"]:
        flag = (
            "which one?" if l["ambiguous"] else "not found" if l["unresolved"] else f"min {l['moq_cases']}" if l["below_moq"]
            else "no price" if l["missing_price"] else f"+{l['cases_to_full_layer']} = full layer" if l["partial_layer"] else ""
        )
        rows.append({"Product": l["name"], "SKU": l["sku"] or l["reference"], "Cases": l["cases"], "Price": money(l["case_price"]), "Total": money(l["line_total"]), "": flag})
    st.table(rows)
    disc = d["discount"]
    disc_txt = "not yet decided" if not disc else "none" if disc["kind"] == "none" else (f"{disc['value']:g}%" if disc["kind"] == "percent" else money(disc["value"]))
    c1, c2 = st.columns(2)
    c1.markdown(f"**Payment** {d['payment_terms']}  \n**Incoterms** {d['incoterms']}  \n**Shipping** {d['shipping_method']}")
    c2.markdown(f"**ETA** {d['eta_date']}  \n**Discount** {disc_txt}" + (f"  \n**Notes** {d['notes']}" if d.get("notes") else ""))
    st.markdown(f"### Total {money(d['total'])}")


def render_gaps(gaps: list[dict[str, Any]]) -> None:
    for i, g in enumerate(gaps):
        st.warning(g["message"])
        if g.get("candidates"):
            cols = st.columns(len(g["candidates"]))
            for col, c in zip(cols, g["candidates"], strict=True):
                if col.button(c["name"], key=f"cand-{i}-{c['sku']}"):
                    st.session_state.pending = f"I mean: {c['name']}"
                    st.rerun()


def main() -> None:
    db, llm = get_backend()
    init_state(db)
    p = st.session_state.partnership

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown(f"**{p['brand_name']}** · {p['distributor_name']} · ships to {p['ship_to_country']}  \n<small>{st.session_state.route}</small>", unsafe_allow_html=True)
        for t in st.session_state.turns:
            with st.chat_message(t["role"]):
                st.write(t["text"])
                for u in t.get("images") or []:
                    st.image(u, width=160)

        prompt = st.chat_input("Tell me what you'd like to order…", accept_file="multiple", file_type=["png", "jpg", "jpeg", "webp"])
        pending = st.session_state.pop("pending", None)
        if prompt is not None or pending:
            text = pending or (prompt.text if hasattr(prompt, "text") else str(prompt))
            files = [] if pending or not hasattr(prompt, "files") else list(prompt.files or [])[:service.MAX_IMAGES]
            send(db, llm, text, [to_data_url(f) for f in files])
            st.rerun()

    with right:
        st.subheader("Order draft")
        render_draft(st.session_state.draft)
        if st.session_state.gaps:
            render_gaps(st.session_state.gaps)

        st.subheader("Progress")
        for s in st.session_state.steps or [{"title": t, "status": "pending"} for t in ("Understand request", "Check inputs", "Confirm draft", "Create & submit order")]:
            st.markdown(f"{STEP_ICON.get(s['status'], '⚪')} {s['title']}")

        if st.session_state.stage == "confirm" and st.session_state.confirm_token:
            if st.button("Confirm — place the order", type="primary", key="confirm"):
                do_confirm(db)
                st.rerun()
        elif st.session_state.stage == "submitted":
            st.success('Order placed. Say "received" or "paid" when the time comes, or start another order.')


main()
