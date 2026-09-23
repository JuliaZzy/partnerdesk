"""Brand knowledge — the brand's own material distilled into fragments the agent may quote.
Upload a deck or catalogue, distil it (two model passes, text layer), then approve the
fragments worth keeping. Only approved fragments will ever reach a prompt."""

from __future__ import annotations

import streamlit as st

from partnerdesk import knowledge
from partnerdesk.config import model_label
from partnerdesk.llm import OpenAICompatLLM
from partnerdesk.ui_pages.common import current_partnership, get_backend, page_tabs, short_date, status_badge

db, llm = get_backend()
p = current_partnership(db)
brand_id = p["brand_id"]
products = db.products(brand_id)
product_names = {x.id: x.display_name for x in products}
if isinstance(llm, OpenAICompatLLM) and st.session_state.get("llm_model"):
    llm = llm.with_model(st.session_state["llm_model"])  # the model picked on the chat page
model_name = getattr(getattr(llm, "settings", None), "model", None)

st.header("Brand knowledge", icon=":material/menu_book:")
st.caption(f"{p['brand_name']} — what the agent is allowed to say about the brand and its products, with the page it came from.")

if flash := st.session_state.pop("kb_flash", None):
    st.success(flash, icon=":material/auto_awesome:")

docs = db.documents(brand_id)
all_fragments = db.fragments(brand_id)

with st.container(horizontal=True):
    st.metric("Documents", len(docs))
    st.metric("Fragments", len(all_fragments))
    st.metric("Approved", len([f for f in all_fragments if f.status == "approved"]))
    st.metric("Awaiting review", len([f for f in all_fragments if f.status == "draft"]))

tab_docs, tab_frags = page_tabs(["Documents", "Fragments"], key="knowledge-tabs")

# --- documents ---------------------------------------------------------------------------------
with tab_docs:
    with st.expander("Add material", icon=":material/upload_file:", expanded=not docs):
        st.caption("PDF (text layer), Word, Markdown or plain text. The file is stored as pages; distilling is a separate step because it costs model calls.")
        up = st.file_uploader("Document", type=["pdf", "docx", "md", "txt"], key="kb_file", label_visibility="collapsed")
        title = st.text_input("Title", key="kb_title", placeholder=up.name if up else "Spring 2026 product deck")
        if st.button("Upload", type="primary", icon=":material/check:", key="kb_upload", disabled=up is None):
            try:
                doc = knowledge.ingest_document(db, brand_id=brand_id, file_name=up.name, data=up.getvalue(), title=title.strip() or None, media_type=up.type)
            except Exception as err:
                st.error(f"Could not read the file: {err}")
            else:
                st.toast(f"Stored {doc.title}: {doc.page_count} page(s)")
                st.rerun()

    if not docs:
        st.info("No material yet. Upload a deck, a catalogue or a brand book.", icon=":material/description:")
    for d in docs:
        frags = [f for f in all_fragments if f.document_id == d.id]
        with st.container(border=True):
            head, actions = st.columns([4, 2])
            with head:
                st.markdown(f"**{d.title or d.file_name}** {status_badge(d.status)}  \n<small>{d.file_name} · {d.page_count or 0} page(s) · uploaded {short_date(d.created_at)}" + (f" · distilled {short_date(d.distilled_at)} with {model_label(d.model) if d.model else '—'}" if d.distilled_at else "") + f" · {len(frags)} fragment(s)</small>", unsafe_allow_html=True)
                if d.summary:
                    st.caption(d.summary)
                sections = db.document_sections(d.id)
                if sections:
                    st.markdown(" ".join(f":blue-badge[{s.type}]" for s in sections))
            with actions, st.container(horizontal=True, horizontal_alignment="right"):
                verb = "Distil again" if d.status == "distilled" else "Distil"
                if st.button(verb, icon=":material/auto_awesome:", key=f"distil-{d.id}", help=f"Two model passes over the text layer with {model_label(model_name) if model_name else 'the chat model'}. Draft fragments are replaced; approved ones stay."):
                    with st.status(f"Distilling {d.title or d.file_name}…", expanded=True) as status:
                        try:
                            result = knowledge.distill_document(db, llm, d.id, products=products)
                        except Exception as err:
                            status.update(label="Distillation failed", state="error")
                            st.error(str(err))
                        else:
                            status.update(label="Distilled", state="complete")
                            st.session_state["kb_flash"] = (
                                f"{d.title or d.file_name}: {len(result.sections)} kind(s) of content, {len(result.fragments)} fragment(s) from {result.pages_processed} page(s) — review them under Fragments."
                                + (f" Skipped: {'; '.join(result.skipped)}" if result.skipped else "")
                            )
                            st.rerun()
                if st.button("Delete", icon=":material/delete:", key=f"deldoc-{d.id}", help="Removes the document and its pages; fragments stay, unlinked."):
                    db.delete_document(d.id)
                    st.rerun()
            with st.expander("Pages", icon=":material/description:"):
                for n, text in db.document_pages(d.id):
                    st.markdown(f"**Page {n}**")
                    st.text(text[:1500] + ("…" if len(text) > 1500 else ""))

# --- fragments ---------------------------------------------------------------------------------
with tab_frags:
    types = sorted({f.type for f in all_fragments})
    with st.container(horizontal=True, vertical_alignment="bottom"):
        status_pick = st.segmented_control("Status", ["all", "draft", "approved", "archived"], default="all", key="kb_status")
        type_pick = st.pills("Type", types, selection_mode="multi", key="kb_types") if types else []
        query = st.text_input("Search", key="kb_search", type="search", placeholder="Search title, content, tags", label_visibility="collapsed")

    shown = [
        f for f in all_fragments
        if (status_pick in (None, "all") or f.status == status_pick)
        and (not type_pick or f.type in type_pick)
        and (not query or query.lower() in f"{f.title} {f.content} {' '.join(f.tags)} {f.product_name or ''}".lower())
    ]
    st.caption(f"{len(shown)} of {len(all_fragments)} fragment(s)")
    for f in shown:
        with st.container(border=True):
            meta = [f":blue-badge[{f.type}]", status_badge(f.status)]
            if f.product_id:
                meta.append(f":green-badge[{product_names.get(f.product_id, f.product_id)}]")
            elif f.product_name:
                meta.append(f":gray-badge[{f.product_name} — not in catalog]")
            st.markdown(f"**{f.title}** " + " ".join(meta))
            st.write(f.content)
            src = next((d.title or d.file_name for d in docs if d.id == f.document_id), None)
            bits = [x for x in (src, f"pages {', '.join(map(str, f.source_pages))}" if f.source_pages else None, ", ".join(f"#{t}" for t in f.tags) if f.tags else None) if x]
            if bits:
                st.caption(" · ".join(bits))
            with st.container(horizontal=True):
                if f.status != "approved" and st.button("Approve", icon=":material/check:", key=f"approve-{f.id}", type="primary"):
                    db.set_fragment_status(f.id, "approved")
                    st.rerun()
                if f.status != "archived" and st.button("Archive", icon=":material/archive:", key=f"archive-{f.id}"):
                    db.set_fragment_status(f.id, "archived")
                    st.rerun()
                if st.button("Delete", icon=":material/delete:", key=f"delfrag-{f.id}"):
                    db.delete_fragment(f.id)
                    st.rerun()

    with st.expander("Add a fragment by hand", icon=":material/add:"), st.form("kb_add", border=False, clear_on_submit=True):
        kind = st.selectbox("Type", list(knowledge.SEED_TYPES), format_func=lambda k: f"{k} — {knowledge.SEED_TYPES[k]}", key="kb_add_type")
        f_title = st.text_input("Title", key="kb_add_title")
        f_content = st.text_area("Content", key="kb_add_content", help="Self-contained: a reader sees only this fragment.")
        f_product = st.text_input("Product (optional)", key="kb_add_product", placeholder="Name or SKU as in the catalog")
        f_tags = st.text_input("Tags (optional, comma-separated)", key="kb_add_tags")
        if st.form_submit_button("Add", icon=":material/check:", type="primary"):
            if f_title.strip() and f_content.strip():
                knowledge.add_fragment(
                    db, brand_id=brand_id, type=kind, title=f_title, content=f_content, product_name=f_product.strip() or None,
                    tags=[t.strip() for t in f_tags.split(",") if t.strip()], products=products,
                )
                st.rerun()
            else:
                st.warning("A title and content are needed.")
