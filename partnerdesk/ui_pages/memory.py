"""Memory — what the agent keeps in mind about this partnership across chats. A chat
remembers eight turns; these outlive the chat and are read into the PO agent's context
on every turn. Context only: a memory can never relax a rule or authorise a discount."""

from __future__ import annotations

import streamlit as st

from partnerdesk.memory import KIND_LABELS, memory_block, remember
from partnerdesk.ui_pages.common import current_partnership, get_backend, short_date

db, _ = get_backend()
p = current_partnership(db)

st.header("Memory", icon=":material/psychology:")
st.caption(f"{p['brand_name']} · {p['distributor_name']} — standing preferences, instructions and facts the agent should carry from one conversation to the next.")

with st.form("mem_add", border=True, clear_on_submit=True):
    st.markdown("**Remember something**")
    with st.container(horizontal=True, vertical_alignment="bottom"):
        kind = st.selectbox("Kind", list(KIND_LABELS), format_func=lambda k: KIND_LABELS[k], key="mem_kind", width=180)
        content = st.text_input("What to remember", key="mem_content", placeholder="Prefers air freight for launches; their warehouse is closed the first week of July", width="stretch")
    if st.form_submit_button("Remember", type="primary", icon=":material/add:"):
        if content.strip():
            remember(db, p["id"], content, kind=kind, source="user")
            st.rerun()
        else:
            st.warning("Write what to remember first.")

active = db.memories(p["id"], active_only=True)
inactive = [m for m in db.memories(p["id"], active_only=False) if not m.is_active]

st.subheader(f"Remembered ({len(active)})")
if not active:
    st.caption("Nothing yet. Whatever you add here is read into the agent's context on its next turn.")
for m in active:
    with st.container(border=True):
        st.markdown(f":blue-badge[{KIND_LABELS.get(m.kind, m.kind)}] {m.content}")
        st.caption(f"from {m.source} · added {short_date(m.created_at)} · last used {short_date(m.last_recalled_at) if m.last_recalled_at else 'never'}")
        with st.container(horizontal=True):
            if st.button("Forget", icon=":material/archive:", key=f"off-{m.id}", help="Kept on file, no longer read into the context."):
                db.set_memory_active(m.id, False)
                st.rerun()
            if st.button("Delete", icon=":material/delete:", key=f"del-{m.id}"):
                db.delete_memory(m.id)
                st.rerun()

if inactive:
    with st.expander(f"Forgotten ({len(inactive)})", icon=":material/history:"):
        for m in inactive:
            with st.container(horizontal=True, vertical_alignment="center"):
                st.markdown(f":gray-badge[{KIND_LABELS.get(m.kind, m.kind)}] {m.content}", width="stretch")
                if st.button("Restore", icon=":material/check:", key=f"on-{m.id}"):
                    db.set_memory_active(m.id, True)
                    st.rerun()
                if st.button("Delete", icon=":material/delete:", key=f"del2-{m.id}"):
                    db.delete_memory(m.id)
                    st.rerun()

with st.expander("What the agent sees", icon=":material/visibility:"):
    st.caption("Appended to the brand's operating context in the extraction and reply prompts of every PO turn.")
    st.code(memory_block(active) or "(nothing — no active memories)", language=None)
