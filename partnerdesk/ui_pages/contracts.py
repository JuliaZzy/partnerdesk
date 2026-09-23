"""Contracts — the agreement behind the partnership: its normalized terms, the PO rules
derived from them, the evidence each field cites, and every extraction run kept verbatim.

Import takes the extraction JSON `legacy/contract_ai` produces (the PDF reader itself isn't
wired into the desk yet) and runs the same `contracts.ingest_extraction` the CLI does.
"""

from __future__ import annotations

import json

import streamlit as st

from partnerdesk.contracts import contract_discount_for_next_order, ingest_extraction
from partnerdesk.ui_pages.common import current_partnership, get_backend, page_tabs, short_date, status_badge

db, _ = get_backend()
p = current_partnership(db)

st.header("Contracts", icon=":material/gavel:")
st.caption(f"{p['brand_name']} · {p['distributor_name']} — terms the code enforces on every order, and where each one came from.")

contracts = db.contracts(p["id"])

with st.expander("Import an extraction", icon=":material/upload_file:"):
    st.caption("The JSON `legacy/contract_ai` writes for an agreement (`{\"extraction\": {...}}` or the extraction object itself). "
               "Stored in full, terms normalized, PO rules derived; the previous active contract is superseded.")
    up = st.file_uploader("Extraction JSON", type=["json"], key="contract_json", label_visibility="collapsed")
    title = st.text_input("Title", key="contract_title", placeholder="Distribution Agreement 2027")
    if st.button("Import", type="primary", icon=":material/check:", key="contract_import", disabled=up is None):
        try:
            data = json.loads(up.getvalue().decode("utf-8"))
            extraction = data.get("extraction", data) if isinstance(data, dict) else None
            if not isinstance(extraction, dict):
                raise ValueError("the file is not a JSON object")
            rec = ingest_extraction(db, extraction, brand_id=p["brand_id"], partnership_id=p["id"], title=title.strip() or up.name, file_name=up.name, model=data.get("model") if isinstance(data, dict) else None)
        except Exception as err:  # a bad file is a message, not a crash
            st.error(f"Could not import: {err}")
        else:
            st.success(f"Imported {rec.contract.title or rec.contract.id}: {len(rec.terms.discount_rules)} discount rule(s), {len(rec.derived_rules)} PO rule(s)." + (f" Skipped: {'; '.join(rec.skipped)}" if rec.skipped else ""))
            st.rerun()

if not contracts:
    st.info("No contract on file for this partnership yet.", icon=":material/description:")
    st.stop()

ids = [c.id for c in contracts]
labels = {c.id: (c.title or c.id) if c.status == "active" else f"{c.title or c.id} ({c.status})" for c in contracts}
chosen = st.selectbox("Contract", ids, format_func=lambda i: labels[i], key="contract_pick", label_visibility="collapsed" if len(ids) == 1 else "visible")
c = next(x for x in contracts if x.id == chosen)
terms = db.contract_terms(c.id)
rules = db.derived_rules(c.id)
runs = db.contract_extractions(c.id)

st.subheader(f"{c.title or c.id} {status_badge(c.status)}")
st.caption(
    " · ".join(x for x in (
        c.contract_type and c.contract_type.replace("_", " "),
        c.file_name,
        f"term {c.term_start_date or '?'} → {c.term_end_date or '?'}" if (c.term_start_date or c.term_end_date) else None,
        "auto-renews" if c.auto_renewal else ("no auto-renewal" if c.auto_renewal is False else None),
        c.exclusivity_type and (c.exclusivity_type if "exclusiv" in c.exclusivity_type.lower() else f"{c.exclusivity_type} exclusivity"),
    ) if x)
)

next_disc = contract_discount_for_next_order(db, p["id"]) if c.status == "active" else None
min_order = next((m for m in terms.moqs if m.quantity and "order" in (m.applies_per or "").lower() and (m.currency or (m.unit or "").upper() in ("USD", "EUR", "CNY", "GBP", "SEK"))), None)
with st.container(horizontal=True):
    st.metric("Next order's discount", f"{next_disc.percent:g}%" if next_disc else "—", help=next_disc.note if next_disc else "Set by the container-sequence schedule of the active contract.")
    st.metric("Discount schedules", len(terms.discount_rules))
    st.metric("Minimum order", f"{min_order.unit or min_order.currency} {min_order.quantity:g}" if min_order else "—")
    excluded = len([t for t in terms.territories if t.kind == "excluded"])
    st.metric("Territories allowed", len([t for t in terms.territories if t.kind == "allowed"]), delta=f"-{excluded} excluded" if excluded else None, delta_color="inverse")
    st.metric("PO rules derived", len(rules))

tab_terms, tab_rules, tab_evidence, tab_runs = page_tabs(
    ["Terms", "PO rules", "Evidence", "Extractions"], key="contract-tabs",
)

with tab_terms:
    if c.territory_text:
        st.markdown(f"**Territory** {c.territory_text}")
    if terms.territories:
        st.markdown(" ".join(f":green-badge[{t.country_code}]" if t.kind == "allowed" else f":red-badge[{t.country_code} excluded]" for t in terms.territories))
    if c.annual_sales_target:
        st.markdown(f"**Annual sales target** {json.dumps(c.annual_sales_target, ensure_ascii=False)} {c.annual_sales_target_currency or ''}")

    st.markdown("**Discount schedules**")
    if not terms.discount_rules:
        st.caption("None extracted.")
    for r in terms.discount_rules:
        with st.container(border=True):
            st.markdown(f"**{r.title or 'Discount rule'}**" + (f" · §{r.section_reference}" if r.section_reference else "") + f"  \n{(r.applies_per or '').replace('_', ' ')} · {(r.basis or '').replace('_', ' ')}")
            st.table([
                {"Containers": f"{t.from_container or 1} – {t.to_container if t.to_container is not None else '∞'}", "Discount": f"{t.discount_percent:g}%" if t.discount_percent is not None else "—", "Notes": t.notes or ""}
                for t in r.tiers
            ])
            if r.source_clause_text:
                st.caption(f"“{r.source_clause_text}”")

    if terms.moqs:
        st.markdown("**Minimum order quantities**")
        st.table([{"Quantity": f"{m.quantity:g}" if m.quantity is not None else "—", "Unit": m.unit or m.currency or "", "Applies per": (m.applies_per or "").replace("_", " "), "Scope": m.product_scope or m.sku or "all", "Clause": m.source_clause_text or ""} for m in terms.moqs])
    if terms.price_list:
        st.markdown("**Price list**")
        st.dataframe([e.model_dump(exclude_none=True) for e in terms.price_list], hide_index=True)

with tab_rules:
    st.caption("What the code-check enforces because of this contract. Each rule carries `contract_id`, so an approver can follow it back to the clause.")
    if not rules:
        st.caption("No rules derived.")
    else:
        st.table([
            {"Rule": r.name, "Type": r.rule_type, "Severity": r.severity, "Config": json.dumps(r.rule_config, ensure_ascii=False), "Active": "yes" if r.is_active else "no"}
            for r in rules
        ])

with tab_evidence:
    if not terms.evidence:
        st.caption("No evidence recorded.")
    else:
        st.table([{"Field": e.field, "Page": e.page or "", "Section": e.section_hint or "", "Quote": e.quote} for e in terms.evidence])

with tab_runs:
    for run in reversed(runs):
        with st.container(border=True):
            st.markdown(f"**{short_date(run['extracted_at'])}** · model {run.get('model') or '—'} · confidence {run.get('confidence_score') if run.get('confidence_score') is not None else '—'}")
            if run.get("extraction_notes"):
                st.caption(run["extraction_notes"])
            if run.get("skipped"):
                st.warning("Not mapped (reported, never guessed): " + "; ".join(run["skipped"]))
            with st.expander("Raw extraction"):
                st.json(run["raw"])
