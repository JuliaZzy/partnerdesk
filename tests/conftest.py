from __future__ import annotations

import pytest

from partnerdesk.catalog import Catalog
from partnerdesk.db import Database
from partnerdesk.models import AgentSettings, CommercialRule, Partnership, Product

# MOQ is stored in UNITS and enforced in CASES: 100 units / 12 per case = 9 cases.
CATALOG_ROWS = [
    dict(id="p-fo", sku="FO-100", product_name_en="Deep Sea Fish Oil", native_name="深海鱼油omega-3", case_pack=12, case_price=100, moq_units=100, cases_per_layer=5),
    dict(id="p-sr", sku="SR-200", product_name_en="Radiance Serum", case_pack=6, case_price=50),
    dict(id="p-srp", sku="SR-201", product_name_en="Radiance Serum Plus", case_pack=6, case_price=60),
    dict(id="p-np", sku="NP-300", product_name_en="Unpriced Item", case_pack=6, case_price=None),
    dict(id="p-gs", sku="GS-010", product_name_en="Gift Set Mini", case_pack=24, case_price=40, commercial_role="gift"),
    dict(id="p-old", sku="OLD-900", product_name_en="Legacy Night Cream", case_pack=6, case_price=45, discontinued=True),
]


@pytest.fixture
def catalog() -> Catalog:
    return Catalog([Product(**r) for r in CATALOG_ROWS])


@pytest.fixture
def settings() -> AgentSettings:
    return AgentSettings()


@pytest.fixture
def partnership() -> Partnership:
    return Partnership(id="ps-1", brand_id="brand-1", distributor_id="dist-1", brand_name="Brand", distributor_name="Dist", ship_to_country="US", currency="USD")


@pytest.fixture
def db(tmp_path, partnership, settings) -> Database:
    d = Database(tmp_path / "test.sqlite3")
    for r in CATALOG_ROWS:
        d.put_document("product", r["id"], {**r, "brand_id": "brand-1"})
    d.put_document("partnership", partnership.id, partnership.model_dump())
    d.put_document("settings", "brand-1", settings.model_dump())
    return d


def rule(**over) -> CommercialRule:
    base = dict(id="r", name="rule", rule_type="min_order_value", rule_config={}, severity="block")
    return CommercialRule(**{**base, **over})
