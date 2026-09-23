"""Reports — the distributor's sell-out and inventory numbers: upload a report, see how
its columns were read and what facts came out, confirm them, and watch the live numbers
(confirmed facts across reports) next to what was ordered."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from partnerdesk import reports, service
from partnerdesk.models import Partnership
from partnerdesk.ui_pages.common import current_partnership, get_backend, short_date, status_badge

db, _ = get_backend()
p = current_partnership(db)
partnership = Partnership(**p)
products = db.products(p["brand_id"])

st.header("Reports", icon=":material/monitoring:")
st.caption(f"{p['brand_name']} · {p['distributor_name']} — every number is read from a cell by code, sits in draft until confirmed, and a re-sent month replaces the earlier one.")


def last_month() -> str:
    t = date.today().replace(day=1)
    y, m = (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)
    return f"{y:04d}-{m:02d}"


# --- upload -------------------------------------------------------------------------------
with st.expander("Upload a report", icon=":material/upload_file:", expanded=not db.reports(p["id"])):
    st.caption("A tidy table for now: one header row, one row per period / product / channel, numeric columns (.xlsx or .csv). "
               "Multi-block workbooks (merged cells, prose between tables) are the next reader — they will feed the same facts.")
    up = st.file_uploader("Report file", type=["xlsx", "xlsm", "csv"], key="report_file", label_visibility="collapsed")
    with st.container(horizontal=True):
        period = st.text_input("Reporting period", value=last_month(), key="report_period", help="YYYY-MM, YYYY-Qn or YYYY. Rows with their own date column override it.")
        title = st.text_input("Title", key="report_title", placeholder=up.name if up else "August sell-out")
        matrix = st.selectbox("Month columns measure", ["(no month columns)", "units", "revenue", "stock"], key="report_matrix",
                              help="Only when months run across the top of the table (9月 | 10月 | …).")
    if up is not None:
        try:
            analysis = reports.analyze(
                up.name, up.getvalue(), reporting_period=period.strip() or None, currency=p["currency"], products=products,
                matrix_metric=None if matrix.startswith("(") else matrix,
            )
        except Exception as err:
            st.error(f"Could not read the file: {err}")
            analysis = None
        if analysis:
            for m in analysis.mappings:
                st.markdown(f"**{m.sheet}** · header on row {m.header_row + 1}" + (f" · currency {m.currency}" if m.currency else ""))
                st.dataframe(m.describe(), hide_index=True, height="content")
            if analysis.facts:
                st.markdown(f"**{len(analysis.facts)} facts** read (draft until you confirm the report)")
                st.dataframe(
                    [{"Metric": f.metric_key, "SKU": f.sku, "Product": f.entity, "Channel": f.channel, "Period": f.period_start, "Value": f.numeric_value, "Unit": f.unit, "Matched": "yes" if f.product_id else "", "Source": f"{f.source_sheet} r{f.source_row} · {f.source_header}"} for f in analysis.facts[:60]],
                    hide_index=True, height=260,
                )
            else:
                st.warning("No facts could be read from this file.")
            if analysis.skipped:
                with st.expander(f"{len(analysis.skipped)} cell(s) or sheet(s) skipped"):
                    for s_ in analysis.skipped:
                        st.caption(s_)
            if st.button("Save as draft", type="primary", icon=":material/save:", key="report_save", disabled=not analysis.facts):
                rep = reports.ingest(db, partnership, analysis, title=title.strip() or None)
                st.session_state["reports_pick"] = rep.id
                st.rerun()

# --- the reports on file ----------------------------------------------------------------------
rows = db.reports(p["id"])
if not rows:
    st.info("No reports yet.", icon=":material/table_chart:")
    st.stop()

st.subheader("On file")
st.dataframe(
    [{"Title": r.title, "Period": r.reporting_period or f"{r.period_start} → {r.period_end}", "Status": r.status, "File": r.file_name, "Uploaded": short_date(r.created_at), "Confirmed": short_date(r.confirmed_at)} for r in rows],
    hide_index=True, height="content",
)
ids = [r.id for r in rows]
labels = {r.id: f"{r.title} · {r.reporting_period or r.period_start or ''}" for r in rows}
chosen = st.selectbox("Report", ids, format_func=lambda i: labels[i], key="reports_pick")
r = next(x for x in rows if x.id == chosen)
facts = db.report_facts(r.id)
runs = db.report_extractions(r.id)

st.markdown(f"**{r.title}** {status_badge(r.status)} · {r.file_name} · {len(facts)} facts")
with st.container(horizontal=True):
    if r.status == "draft":
        if st.button("Confirm facts", type="primary", icon=":material/fact_check:", key=f"confirm-{r.id}", help="Drafts become the live numbers; an earlier report's facts for the same measurement and period are superseded."):
            out = db.confirm_report(r.id)
            st.toast(f"Confirmed {out['confirmed']} facts" + (f", superseded {out['superseded']}" if out["superseded"] else ""))
            st.rerun()
    if st.button("Delete report", icon=":material/delete:", key=f"delete-{r.id}"):
        db.delete_report(r.id)
        st.session_state.pop("reports_pick", None)
        st.rerun()

if facts:
    st.dataframe(
        [{"Status": f.status, "Metric": f.metric_key, "SKU": f.sku, "Product": f.entity, "Channel": f.channel, "Period": f.period_start, "Value": f.numeric_value, "Unit": f.unit, "Source": f"{f.source_sheet} r{f.source_row} · {f.source_header}"} for f in facts],
        hide_index=True, height=300,
    )
for run in runs:
    with st.expander(f"How it was read · {run['method']} · {short_date(run['extracted_at'])}"):
        for t in run["raw"].get("tables", []):
            st.markdown(f"**{t['sheet']}** · header row {t['header_row'] + 1}" + (f" · {t['currency']}" if t.get("currency") else ""))
            st.dataframe(t["columns"], hide_index=True, height="content")
        for s_ in run.get("skipped") or []:
            st.caption(s_)

# --- the live numbers ---------------------------------------------------------------------------
live = db.confirmed_facts(p["id"])
st.subheader("Live numbers")
if not live:
    st.caption("Confirm a report to see its numbers here.")
    st.stop()

df = pd.DataFrame([f.model_dump() for f in live])
df["month"] = df["period_start"].str.slice(0, 7)
sales = df[df["fact_type"] == "sales"]
revenue = sales[sales["metric_key"] == "revenue"]
units = sales[sales["metric_key"] == "units_sold"]
stock = df[df["metric_key"] == "stock_on_hand"]

cur = next((c for c in revenue["currency"].dropna().unique()), p["currency"])
with st.container(horizontal=True):
    st.metric("Revenue", f"{cur} {revenue['numeric_value'].sum():,.0f}" if not revenue.empty else "—")
    st.metric("Units sold", f"{units['numeric_value'].sum():,.0f}" if not units.empty else "—")
    st.metric("Months covered", df["month"].nunique())
    st.metric("SKUs seen", int(sales["sku"].nunique()))

c1, c2 = st.columns(2, gap="large")
with c1:
    if not revenue.empty:
        st.markdown(f"**Revenue by month** ({cur})")
        by_month = revenue.groupby("month", as_index=False)["numeric_value"].sum().rename(columns={"numeric_value": "revenue"})
        st.bar_chart(by_month, x="month", y="revenue", x_label="", y_label="")
    if not units.empty and units["channel"].notna().any():
        st.markdown("**Units by channel**")
        by_channel = units.groupby(["month", "channel"], as_index=False)["numeric_value"].sum().rename(columns={"numeric_value": "units"})
        st.bar_chart(by_channel, x="month", y="units", color="channel", x_label="", y_label="", stack=True)
with c2:
    if not units.empty:
        st.markdown("**Units by product**")
        by_sku = units.assign(label=units["entity"].fillna(units["sku"])).groupby("label", as_index=False)["numeric_value"].sum().rename(columns={"numeric_value": "units"}).sort_values("units", ascending=False).head(12)
        st.bar_chart(by_sku, x="label", y="units", horizontal=True, x_label="", y_label="")
    if not stock.empty:
        st.markdown("**Stock on hand (latest)**")
        latest = stock.sort_values("period_start").groupby("sku", as_index=False).last()
        st.dataframe(latest[["sku", "entity", "period_start", "numeric_value"]].rename(columns={"entity": "product", "period_start": "as of", "numeric_value": "units"}), hide_index=True, height="content")

# Sell-in (what they ordered from the brand) next to sell-out (what they report selling), per SKU.
ordered: dict[str, int] = {}
names: dict[str, str] = {}
for o in service.orders(db, p["id"]):
    for i in o["items"]:
        ordered[i["sku"]] = ordered.get(i["sku"], 0) + i["quantity"] * (i["case_pack"] or 1)
        names[i["sku"]] = i["product_name"]
if ordered and not units.empty:
    st.markdown("**Ordered vs sold, by SKU** (units; orders × case pack)")
    sold = units.dropna(subset=["sku"]).groupby("sku")["numeric_value"].sum()
    skus = sorted(set(ordered) | set(sold.index))
    st.dataframe(
        [{"SKU": s, "Product": names.get(s) or next((f.entity for f in live if f.sku == s and f.entity), ""), "Ordered": ordered.get(s, 0), "Sold": float(sold.get(s, 0.0)), "Sell-through": (float(sold.get(s, 0.0)) / ordered[s]) if ordered.get(s) else None} for s in skus],
        column_config={"Sell-through": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1)},
        hide_index=True, height="content",
    )
