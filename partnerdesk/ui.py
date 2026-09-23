"""The desk — pure Python (Streamlit), no JavaScript.

    streamlit run partnerdesk/ui.py

This file is the chat page and the app's entry point: `desk()` at the bottom registers
this chat plus the pages in `ui_pages/` (orders, contracts, reports, brand knowledge,
memory) on one navigation, sharing one backend. The links sit in the sidebar under
"New chat".

The chat calls service.py in-process: the same event stream the HTTP API serializes,
rendered with Streamlit's chat widgets. Left: the conversation. Right: the draft the code
built, what's still missing (with one-click answers), the step ledger, and the Confirm button.
"""

from __future__ import annotations

import base64
from typing import Any

import streamlit as st

from partnerdesk import service
from partnerdesk.config import chat_models, default_model_id, model_label
from partnerdesk.db import Database
from partnerdesk.llm import LLM, OpenAICompatLLM
from partnerdesk.po.turn import build_ledger
from partnerdesk.ui_pages.common import NAV_PAGES_KEY, get_backend, pick_partnership, sidebar_nav

st.set_page_config(
    page_title="partnerdesk", page_icon="📦", layout="wide", initial_sidebar_state="expanded",
)

STEP_ICON = {"done": "✅", "working": "🔄", "needs-you": "🟡", "pending": "⚪"}


def pick_model() -> str:
    """Compact picker beside the chat input, bottom-aligned if the input grows."""
    models = chat_models()
    ids = [m.id for m in models]
    labels = {m.id: (m.short or m.label) for m in models}
    if "llm_model" not in st.session_state:
        chosen = default_model_id()
        st.session_state.llm_model = chosen if chosen in ids else ids[0]
    selected = st.selectbox(
        "Model",
        options=ids,
        format_func=lambda i: labels[i],
        key="llm_model",
        width=160,
        label_visibility="collapsed",
        help="Applies to the next message.",
    )
    return selected or ids[0]


_UI = ("turns", "draft", "gaps", "steps", "stage", "confirm_token", "route")
_SKIP_HISTORY = {"(message)", "(confirm)"}


def _greeting(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "text": (
            f"Hi — I can place an order for {p['distributor_name']} with {p['brand_name']}. "
            "What would you like, and how many cases?"
        ),
    }


def _snapshot() -> dict[str, Any]:
    return {k: st.session_state.get(k) for k in _UI}


def _apply(data: dict[str, Any]) -> None:
    st.session_state.update(data)


def save_open_chat() -> None:
    sid = st.session_state.get("sid")
    if sid:
        st.session_state.setdefault("chats", {})[sid] = _snapshot()


def bump_chat(sid: str) -> None:
    order = st.session_state.setdefault("chat_order", [])
    if sid in order:
        order.remove(sid)
    order.insert(0, sid)


def chat_title(sid: str, row: dict[str, Any] | None = None) -> str:
    turns = (st.session_state.get("chats") or {}).get(sid, {}).get("turns") or []
    for t in turns:
        text = (t.get("text") or "").strip()
        if t.get("role") == "user" and text:
            return text.replace("\n", " ")[:36] + ("…" if len(text) > 36 else "")
    for m in (row or {}).get("history") or []:
        text = str(m.get("content") or "").strip()
        if m.get("role") == "user" and text and text not in _SKIP_HISTORY:
            return text.replace("\n", " ")[:36] + ("…" if len(text) > 36 else "")
    return "New chat"


def load_chat(db: Database, sid: str) -> None:
    cached = (st.session_state.get("chats") or {}).get(sid)
    if cached:
        st.session_state.sid = sid
        _apply(cached)
        return
    session = db.session(sid)
    if not session:
        return
    p = st.session_state.partnership
    turns = [_greeting(p)]
    for m in session.get("history") or []:
        role, content = m.get("role"), m.get("content")
        if role in ("user", "assistant") and str(content or "") not in _SKIP_HISTORY:
            turns.append({"role": role, "text": str(content)})
    st.session_state.sid = sid
    stage = session.get("stage") or "gathering"
    _apply({
        "turns": turns, "draft": None, "gaps": [], "steps": build_ledger(stage),
        "stage": stage, "confirm_token": session.get("confirm_token"), "route": "",
    })
    save_open_chat()


def start_chat(db: Database) -> None:
    p = st.session_state.partnership
    session = service.new_session(db, p["id"])
    st.session_state.sid = session["id"]
    _apply({
        "turns": [_greeting(p)], "draft": None, "gaps": [], "steps": [],
        "stage": "gathering", "confirm_token": None, "route": "",
    })
    bump_chat(session["id"])
    save_open_chat()


def render_chat_nav(db: Database) -> None:
    with st.sidebar:
        if st.button("New chat", icon=":material/add:", width="stretch", key="new-chat"):
            save_open_chat()
            start_chat(db)
            st.rerun()
        sidebar_nav()  # the other desk pages, right under "New chat"
        st.caption("Chats")
        pid = st.session_state.partnership["id"]
        rows = {r["id"]: r for r in db.list_sessions(pid)}
        order = st.session_state.setdefault("chat_order", [])
        for sid in rows:
            if sid not in order:
                order.append(sid)
        current = st.session_state.sid
        for sid in order:
            if st.button(
                chat_title(sid, rows.get(sid)),
                key=f"open-{sid}",
                width="stretch",
                type="primary" if sid == current else "secondary",
            ) and sid != current:
                save_open_chat()
                load_chat(db, sid)
                st.rerun()


def init_state(db: Database) -> None:
    if "sid" in st.session_state:
        return
    partnership = db.partnerships()[0].model_dump()
    st.session_state.partnership = partnership
    st.session_state.setdefault("chats", {})
    st.session_state.setdefault("chat_order", [])
    st.session_state.pending = None
    existing = db.list_sessions(partnership["id"])
    if existing:
        st.session_state.chat_order = [r["id"] for r in existing]
        load_chat(db, existing[0]["id"])
    else:
        start_chat(db)


def to_data_url(file: Any) -> str:
    mime = getattr(file, "type", None) or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(file.getvalue()).decode()}"


def apply_done(d: dict[str, Any]) -> None:
    st.session_state.update(
        stage=d.get("stage") or st.session_state.stage, draft=d.get("draft"), gaps=d.get("gaps_detail") or [],
        steps=d.get("steps") or st.session_state.steps, confirm_token=d.get("confirm_token"),
    )


def confirm_ready(steps: list[dict[str, Any]] | None) -> bool:
    by = {s.get("title"): s.get("status") for s in (steps or [])}
    return by.get("Understand request") == "done" and by.get("Check inputs") == "done"


def _bubble_body(t: dict[str, Any]) -> None:
    if t.get("model"):
        st.caption(model_label(t["model"]))
    st.markdown(t.get("text") or "")
    for u in t.get("images") or []:
        st.image(u, width=160)


def _message_row(is_user: bool):
    """Pack the bubble toward the speaker: assistant left, you right."""
    return st.container(
        horizontal=True,
        horizontal_alignment="right" if is_user else "left",
        vertical_alignment="top",
        gap="small",
        wrap=False,
    )


def render_turn(t: dict[str, Any]) -> None:
    """Assistant avatar on the left, user avatar on the right."""
    is_user = t.get("role") == "user"
    icon = ":material/person:" if is_user else ":material/smart_toy:"
    with _message_row(is_user):
        if not is_user:
            st.markdown(icon)
        with st.container(border=True, width="content"):
            _bubble_body(t)
        if is_user:
            st.markdown(icon)


def send(db: Database, llm: LLM, text: str, images: list[str]) -> None:
    user_turn = {"role": "user", "text": text, "images": images}
    st.session_state.turns.append(user_turn)
    render_turn(user_turn)
    with _message_row(False):
        st.markdown(":material/smart_toy:")
        with st.container(border=True, width="content"):
            box = st.empty()
            box.caption("Working…")
            done: dict[str, Any] = {}
            parts: list[str] = []
            for e in service.handle_message(db, llm, st.session_state.sid, text, images):
                if e["type"] == "token":
                    parts.append(e["text"])
                    box.markdown("".join(parts))
                elif e["type"] == "route":
                    st.session_state.route = f"→ {e['specialist'] or 'abstain'} · {e['reason']}"
                elif e["type"] == "warning":
                    st.caption(f"⚠ {e['message']}")
                elif e["type"] == "done":
                    done.update(e)
            reply = "".join(parts)
            if not reply:
                box.markdown("_(no reply from the model)_")
    model = getattr(getattr(llm, "settings", None), "model", None)
    st.session_state.turns.append({"role": "assistant", "text": reply, "model": model})
    if done:
        apply_done(done)
    bump_chat(st.session_state.sid)
    save_open_chat()


def do_confirm(db: Database) -> None:
    r = service.confirm(db, st.session_state.sid, st.session_state.confirm_token)
    if r["state"] in ("submitted", "changed"):
        st.session_state.turns.append({"role": "assistant", "text": r["message"]})
        st.session_state.stage = "submitted" if r["state"] == "submitted" else "gathering"
        st.session_state.draft, st.session_state.steps, st.session_state.gaps = r.get("draft"), r.get("steps") or [], []
    else:
        st.session_state.turns.append({"role": "assistant", "text": f"Confirm: {r['state']}."})
    st.session_state.confirm_token = None
    bump_chat(st.session_state.sid)
    save_open_chat()


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
    if disc and disc.get("source") == "contract":
        disc_txt += " · per contract"
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
                if col.button(c["name"], key=f"cand-{st.session_state.sid}-{i}-{c['sku']}"):
                    st.session_state.pending = f"I mean: {c['name']}"
                    st.rerun()


def render_side_panel(db: Database) -> None:
    """Draft + progress stay on screen; chat scrolls independently."""
    with st.container(key="order-panel"):
        st.subheader("Order draft")
        render_draft(st.session_state.draft)
        if st.session_state.gaps:
            render_gaps(st.session_state.gaps)

        st.subheader("Progress")
        steps = st.session_state.steps or [
            {"title": t, "status": "pending"}
            for t in ("Understand request", "Check inputs", "Confirm draft", "Create & submit order")
        ]
        for s in steps:
            st.markdown(f"{STEP_ICON.get(s['status'], '⚪')} {s['title']}")

        if st.session_state.stage == "confirm" and st.session_state.confirm_token:
            ready = confirm_ready(st.session_state.steps)
            if st.button(
                "Confirm — place the order",
                type="primary",
                key=f"confirm-{st.session_state.sid}",
                disabled=not ready,
            ):
                do_confirm(db)
                st.rerun()
        elif st.session_state.stage == "submitted":
            st.success('Order placed. Say "received" or "paid" when the time comes, or start another order.')


def inject_layout_css() -> None:
    st.html("""
<style>
.st-key-chat-thread > div {
  height: calc(100dvh - 10.5rem) !important;
  max-height: calc(100dvh - 10.5rem) !important;
}
.st-key-order-panel {
  position: sticky;
  top: 0.75rem;
  z-index: 2;
  max-height: calc(100dvh - 10.5rem);
  overflow: auto;
}
/* The model picker sits inside the chat input, just left of Send: the selectbox is laid
   over the input's right end and the textarea keeps clear of it. */
.st-key-composer { position: relative; }
.st-key-composer [data-testid="stElementContainer"]:has([data-testid="stSelectbox"]) {
  position: absolute; right: 3.6rem; bottom: 0.5rem; width: 10rem; z-index: 3; margin: 0;
}
.st-key-composer [data-testid="stSelectbox"] [data-baseweb="select"] > div {
  background: transparent; border-color: transparent; box-shadow: none; min-height: 2.1rem;
}
.st-key-composer [data-testid="stChatInput"] textarea { padding-right: 11rem !important; }
</style>
""")


def main() -> None:
    db, llm = get_backend()
    init_state(db)
    render_chat_nav(db)
    inject_layout_css()
    p = st.session_state.partnership

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown(f"**{p['brand_name']}** · {p['distributor_name']} · ships to {p['ship_to_country']}  \n<small>{st.session_state.route}</small>", unsafe_allow_html=True)
        chat = st.container(height=720, autoscroll=True, border=False, key="chat-thread")
        with chat:
            for t in st.session_state.turns:
                render_turn(t)
            live = st.container()

    with right:
        render_side_panel(db)

    with st.bottom, st.container(key="composer"):
        prompt = st.chat_input(
            "Tell me what you'd like to order…",
            accept_file="multiple",
            file_type=["png", "jpg", "jpeg", "webp"],
            width="stretch",
        )
        model_id = pick_model()  # drawn inside the input box, left of Send — see inject_layout_css

    pending = st.session_state.pop("pending", None)
    if prompt is not None or pending:
        if isinstance(llm, OpenAICompatLLM):
            llm = llm.with_model(model_id)
        text = pending or (prompt.text if hasattr(prompt, "text") else str(prompt))
        files = [] if pending or not hasattr(prompt, "files") else list(prompt.files or [])[:service.MAX_IMAGES]
        with live:
            send(db, llm, text, [to_data_url(f) for f in files])
        st.rerun()


def desk() -> None:
    """This chat plus the pages in ui_pages/ on one navigation. The menu is drawn by
    `sidebar_nav` (under "New chat" here, at the top of the sidebar elsewhere), so the
    built-in one is hidden. Every page reads the same backend from session_state."""
    db, _ = get_backend()
    pick_partnership(db)
    pages = [
        st.Page(main, title="Chat", icon=":material/chat:", default=True),
        st.Page("ui_pages/orders.py", title="Orders", icon=":material/receipt_long:"),
        st.Page("ui_pages/contracts.py", title="Contracts", icon=":material/gavel:"),
        st.Page("ui_pages/reports.py", title="Reports", icon=":material/monitoring:"),
        st.Page("ui_pages/knowledge.py", title="Brand knowledge", icon=":material/menu_book:"),
        st.Page("ui_pages/memory.py", title="Memory", icon=":material/psychology:"),
    ]
    st.session_state[NAV_PAGES_KEY] = pages
    page = st.navigation(pages, position="hidden")
    if page.title != "Chat":
        sidebar_nav()
    page.run()


desk()
