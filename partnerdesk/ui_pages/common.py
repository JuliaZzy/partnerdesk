"""Shared by every page of the desk (ui.py and ui_pages/*): the backend, the current
partnership, the sidebar links, and a few formatters. Not a page itself."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import streamlit as st

from partnerdesk.db import Database
from partnerdesk.llm import LLM, OpenAICompatLLM

BACKEND_KEY = "_backend"  # tests inject (db, llm) here; get_backend sets it for the real app


@st.cache_resource(show_spinner="Opening the database…")
def _real_backend() -> tuple[Database, LLM]:
    db = Database()
    db.seed_from_fixtures()
    return db, OpenAICompatLLM()


def _current(backend: tuple[Any, Any]) -> bool:
    """False after a hot reload of the data layer: Streamlit re-imports `Database`, but a
    cached instance still belongs to the OLD class and lacks any method added since —
    the "'Database' object has no attribute …" a running dev server shows otherwise."""
    return isinstance(backend[0], Database)


def get_backend() -> tuple[Database, LLM]:
    """One (db, llm) pair per process, shared by every page through session_state. Tests
    inject theirs under the same key."""
    cached = st.session_state.get(BACKEND_KEY)
    if cached is not None and _current(cached):
        return cached
    backend = _real_backend()
    if not _current(backend):
        _real_backend.clear()
        backend = _real_backend()
    st.session_state[BACKEND_KEY] = backend
    return backend


def current_partnership(db: Database) -> dict[str, Any]:
    """The partnership every page works on. The chat page sets it on its first run; any
    other page landed on first picks the first one, the same way the chat page does."""
    p = st.session_state.get("partnership")
    if not p:
        p = db.partnerships()[0].model_dump()
        st.session_state.partnership = p
    return p


def pick_partnership(db: Database) -> dict[str, Any]:
    """Sidebar control, shown only when there are several partnerships to choose from.
    Switching clears the chat page's per-partnership state so it re-opens on the new one."""
    partnerships = db.partnerships()
    current = current_partnership(db)
    if len(partnerships) <= 1:
        return current
    with st.sidebar:
        ids = [p.id for p in partnerships]
        labels = {p.id: f"{p.brand_name} · {p.distributor_name}" for p in partnerships}
        chosen = st.selectbox("Partnership", ids, index=ids.index(current["id"]) if current["id"] in ids else 0, format_func=lambda i: labels[i], key="partnership_pick")
    if chosen != current["id"]:
        st.session_state.partnership = next(p for p in partnerships if p.id == chosen).model_dump()
        for k in ("sid", "chats", "chat_order", "turns", "draft", "gaps", "steps", "stage", "confirm_token", "route", "pending"):
            st.session_state.pop(k, None)
        st.rerun()
    return current


NAV_PAGES_KEY = "_nav_pages"  # the st.Page list ui.py registered, so any page can draw the links


def keep_tabs_from_jumping() -> None:
    """Stop a tab click from yanking the page scrollbar to the top.

    A short tab used to collapse the page; the browser then clamped scroll to 0.
    Tabs also focus the new panel, which scrolls it into view. Pin the panel
    height and restore scroll after the click.
    """
    st.html(
        """
<style>
.stApp, [data-testid="stMain"], [data-testid="stMainBlockContainer"] {
  overflow-anchor: none;
}
[data-baseweb="tab-panel"]:focus { outline: none; }
</style>
<script>
(function () {
  const doc = document;
  if (doc.documentElement.dataset.tabScrollLock) return;
  doc.documentElement.dataset.tabScrollLock = "1";
  const targets = () => [
    doc.scrollingElement, doc.documentElement, doc.body,
    doc.querySelector("[data-testid='stAppViewContainer']"),
    doc.querySelector("[data-testid='stMain']"),
    doc.querySelector("[data-testid='stMainBlockContainer']"),
  ].filter(Boolean);
  const restore = (saved) => saved.forEach(({ el, top }) => { el.scrollTop = top; });
  doc.addEventListener("mousedown", (ev) => {
    if (!ev.target.closest('[role="tab"]')) return;
    const saved = targets().map((el) => ({ el, top: el.scrollTop }));
    requestAnimationFrame(() => { restore(saved); requestAnimationFrame(() => restore(saved)); });
    setTimeout(() => restore(saved), 0);
    setTimeout(() => restore(saved), 50);
  }, true);
})();
</script>
""",
        unsafe_allow_javascript=True,
    )


def page_tabs(labels: Sequence[str], *, key: str):
    """In-page tabs that keep their selection when another widget reruns the page.

    Streamlit's default tabs are client-only, so a Status filter (or Approve)
    was snapping back to the first tab. A side key remembers the active tab;
    we restore it unless this run was caused by the tab bar itself.
    """
    keep_tabs_from_jumping()
    names = list(labels)
    remember = f"{key}__active"
    clicked = f"{key}__clicked"

    def _clicked() -> None:
        st.session_state[remember] = st.session_state[key]
        st.session_state[clicked] = True

    if not st.session_state.pop(clicked, False):
        saved = st.session_state.get(remember)
        if isinstance(saved, str) and saved in names:
            st.session_state[key] = saved

    tabs = st.tabs(names, key=key, height=640, on_change=_clicked)
    if remember not in st.session_state:
        st.session_state[remember] = st.session_state.get(key, names[0])
    return tabs


def sidebar_nav() -> None:
    """The desk's page links, drawn where the calling page wants them in the sidebar (the
    chat page puts them under its "New chat" button). Navigation itself is `position="hidden"`
    so this is the only menu."""
    pages = st.session_state.get(NAV_PAGES_KEY) or []
    if len(pages) < 2:
        return
    with st.sidebar:
        for pg in pages:
            st.page_link(pg, label=pg.title, icon=pg.icon or None, width="stretch")


def money(currency: str | None, n: float | None) -> str:
    return "—" if n is None else f"{currency or ''} {n:,.2f}".strip()


def status_badge(status: str | None) -> str:
    """Inline markdown badge for the statuses the desk shows."""
    color = {
        "active": "green", "approved": "green", "confirmed": "green", "submitted": "green", "distilled": "green",
        "draft": "orange", "uploaded": "orange", "gathering": "orange", "confirm": "orange", "pending_approval": "orange",
        "superseded": "gray", "archived": "gray", "failed": "red", "cancelled": "red",
    }.get(status or "", "blue")
    return f":{color}-badge[{status or '—'}]"


def short_date(ts: datetime | None) -> str:
    return ts.date().isoformat() if ts else "—"
