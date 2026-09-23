"""Orders — every purchase order this partnership placed through the chat, with its lines
and what happened after it (received, paid, claims). Read-only: orders are made in chat."""

from __future__ import annotations

import streamlit as st

from partnerdesk import service
from partnerdesk.ui_pages.common import current_partnership, get_backend, money, short_date, status_badge

db, _ = get_backend()
p = current_partnership(db)

st.header("Orders", icon=":material/receipt_long:")
st.caption(f"{p['brand_name']} · {p['distributor_name']} — placed through the chat, gated by the code-check and a Confirm.")

orders = service.orders(db, p["id"])
if not orders:
    st.info("No orders yet. Place one in the chat — the draft, the rule check and the Confirm button all live there.", icon=":material/chat:")
    st.stop()

cur = orders[0]["currency"]
open_orders = [o for o in orders if o["status"] not in ("completed", "cancelled")]
with st.container(horizontal=True):
    st.metric("Orders", len(orders))
    st.metric("Open", len(open_orders))
    st.metric("Ordered", money(cur, sum(o["total_amount"] for o in orders)))
    st.metric("Cases", sum(i["quantity"] for o in orders for i in o["items"]))

st.dataframe(
    [
        {
            "PO": o["po_number"], "Status": o["status"], "Total": o["total_amount"], "Discount": o["discount_percentage"],
            "Lines": len(o["items"]), "Submitted": short_date(o["submitted_at"]), "ETA": o["eta_date"] or "—",
            "Terms": f"{o['payment_terms']} · {o['incoterms']} · {o['shipping_method']}",
        }
        for o in orders
    ],
    column_config={
        "Total": st.column_config.NumberColumn(format=f"{cur} %.2f"),
        "Discount": st.column_config.NumberColumn(format="%.1f%%"),
    },
    hide_index=True,
)

numbers = [o["po_number"] for o in orders]
chosen = st.selectbox("Order", numbers, key="orders_pick")
o = next(x for x in orders if x["po_number"] == chosen)

st.subheader(f"{o['po_number']} {status_badge(o['status'])}")
left, right = st.columns([3, 2], gap="large")
with left:
    st.dataframe(
        [{"Product": i["product_name"], "SKU": i["sku"], "Cases": i["quantity"], "Case price": i["case_price"], "Line total": i["line_total"]} for i in o["items"]],
        column_config={
            "Case price": st.column_config.NumberColumn(format=f"{cur} %.2f"),
            "Line total": st.column_config.NumberColumn(format=f"{cur} %.2f"),
        },
        hide_index=True,
    )
    disc = f"{o['discount_percentage']:g}%" if o["discount_percentage"] else money(cur, o["discount_amount"]) if o["discount_amount"] else "none"
    st.markdown(
        f"**Subtotal** {money(cur, o['subtotal_amount'])} · **Discount** {disc} · **Total** {money(cur, o['total_amount'])}  \n"
        f"**Payment** {o['payment_terms']} · **Incoterms** {o['incoterms']} · **Shipping** {o['shipping_method']} · **ETA** {o['eta_date'] or '—'} · **Ship to** {o['ship_to_country'] or '—'}"
        + (f"  \n**Notes** {o['notes']}" if o.get("notes") else "")
    )
    if o["prepaid_amount"] or o["balance_paid"]:
        st.caption(f"Prepaid {money(cur, o['prepaid_amount'])} · balance paid {money(cur, o['balance_paid'])}")
with right:
    st.markdown("**Timeline**")
    events = [{"kind": "submitted", "created_at": o["submitted_at"], "data": {}}] if o["submitted_at"] else []
    events += o["events"]
    if not events:
        st.caption("Nothing yet.")
    for e in events:
        detail = ", ".join(f"{k} {v}" for k, v in (e.get("data") or {}).items() if v not in (None, "", [], {}))
        st.markdown(f"- `{short_date(e['created_at'])}` **{e['kind']}**" + (f" — {detail}" if detail else ""))
