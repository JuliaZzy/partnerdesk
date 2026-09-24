"""Products — the whole catalog across every brand: both names, pack, case price, MOQ, pallet
pattern, status and policy tags. Read-only: `dev catalog` loads it.

Hundreds of thousands of rows, so filtering, counting and paging happen in the database
(`Database.search_catalog`); the page only ever holds one page of products. It needs no
partnership — looking up a product shouldn't depend on having a contract with its brand."""

from __future__ import annotations

import math

import streamlit as st

from partnerdesk.models import PRODUCT_STATUS_VALUES
from partnerdesk.ui_pages.common import get_backend

PAGE_SIZE = 100

db, _ = get_backend()

st.header("Products", icon=":material/inventory_2:")
st.caption("Every brand's catalog — the products the chat can quote, with the numbers the code-check uses.")


def _back_to_first_page() -> None:
    st.session_state.products_page = 1


with st.container(horizontal=True, vertical_alignment="bottom"):
    query = st.text_input("Search", placeholder="SKU, English or native name", key="products_q", on_change=_back_to_first_page)
    brand = st.text_input("Brand", placeholder="Brand name", key="products_brand", on_change=_back_to_first_page)
    category = st.selectbox("Category", db.catalog_categories(), index=None, placeholder="All", key="products_cat", on_change=_back_to_first_page)
    status = st.selectbox("Status", PRODUCT_STATUS_VALUES, index=None, placeholder="All", key="products_status", on_change=_back_to_first_page)

filters = {"brand": brand, "category": category, "status": status}
stats = db.catalog_stats(query, **filters)
total = sum(stats["by_status"].values())
if not total:
    st.info("No products match." if any((query, brand, category, status)) else "The catalog is empty — load it with `dev catalog`.",
            icon=":material/inventory_2:")
    st.stop()

with st.container(horizontal=True):
    st.metric("Products", f"{total:,}")
    st.metric("Brands", f"{stats['brands']:,}")
    st.metric("Categories", stats["categories"])
    for s in PRODUCT_STATUS_VALUES:
        st.metric(s, f"{stats['by_status'].get(s, 0):,}")

pages = max(1, math.ceil(total / PAGE_SIZE))
# The page widget owns this key; clamp it before the widget is drawn (a filter can shrink `pages`).
st.session_state.products_page = min(max(1, st.session_state.get("products_page", 1)), pages)
page = st.session_state.products_page
rows, _ = db.search_catalog(query, **filters, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)

st.dataframe(
    [
        {
            "SKU": x["sku"], "English name": x["product_name_en"], "Native name": x["product_name_native"],
            "Brand": x["brand_name"], "Status": x["product_status"], "Category": x["category"],
            "Subcategory": x["subcategory"], "Unit size": x["unit_size"], "Variant": x["variant"], "Form": x["form"],
            "Case pack": x["case_pack"], "Case price": x["case_price"], "MOQ (units)": x["moq_units"],
            "Cases/layer": x["cases_per_layer"], "Layers/pallet": x["layers_per_pallet"],
            "Cases/pallet": x["cases_per_pallet"], "Case kg": x["case_weight"], "Policy tags": x["policy_tags"],
            "Restricted in": x["restricted_territories"], "Description": x["description_en"],
        }
        for x in rows
    ],
    column_config={
        # Prices are in the brand's currency; catalog brands all quote USD today.
        "Case price": st.column_config.NumberColumn(format="$%.2f"),
        "Case kg": st.column_config.NumberColumn(format="%.1f"),
        "Policy tags": st.column_config.ListColumn(),
        "Restricted in": st.column_config.ListColumn(),
        "Description": st.column_config.TextColumn(width="large"),
    },
    hide_index=True,
    height=640,
)

with st.container(horizontal=True, vertical_alignment="center"):
    st.number_input("Page", min_value=1, max_value=pages, step=1, key="products_page", width=140)
    first = (page - 1) * PAGE_SIZE + 1
    st.caption(f"{first:,}–{first + len(rows) - 1:,} of {total:,} · {pages:,} pages")
