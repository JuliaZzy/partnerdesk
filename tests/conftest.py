"""Shared fixtures.

`make_db` is the one way tests get a database. It hands back a `Database` on an empty store:
a temp SQLite file by default, or a throwaway Postgres database when `PARTNERDESK_TEST_DB_URL`
points at a server. Asking for the same `name` twice returns the same store, so a test can
close a database and reopen it. Everything is dropped at teardown.

Running the suite on both engines is the point: SQLite keeps the fast default, Postgres is
what the app actually deploys on, and a divergence between them should fail here.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from partnerdesk.catalog import Catalog
from partnerdesk.config import env
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


def _server_url(server: str, database: str) -> str:
    return make_url(server).set(database=database).render_as_string(hide_password=False)


def _admin(server: str, statement: str) -> None:
    """CREATE/DROP DATABASE cannot run inside a transaction — hence AUTOCOMMIT."""
    engine = create_engine(server, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(statement))
    finally:
        engine.dispose()


@pytest.fixture
def make_db(tmp_path):
    server = env("PARTNERDESK_TEST_DB_URL")
    stores: dict[str, str | object] = {}
    live: list[Database] = []

    def build(name: str = "test") -> Database:
        if name not in stores:
            if server:
                stores[name] = f"pdtest_{uuid.uuid4().hex[:12]}"
                _admin(server, f'CREATE DATABASE "{stores[name]}"')
            else:
                stores[name] = tmp_path / f"{name}.sqlite3"
        target = stores[name]
        d = Database(_server_url(server, target) if server else target)
        live.append(d)
        return d

    yield build

    # Postgres refuses to drop a database with sessions still on it, so every pool goes first.
    for d in live:
        d.engine.dispose()
    if server:
        for database in stores.values():
            _admin(server, f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')


@pytest.fixture
def db(make_db, partnership, settings) -> Database:
    d = make_db()
    d.upsert_partnership(partnership)  # creates brand-1 / dist-1 from the names on it
    for r in CATALOG_ROWS:
        d.upsert_product("brand-1", Product(**r))
    d.put_settings("brand-1", settings)
    return d


def rule(**over) -> CommercialRule:
    base = dict(id="r", name="rule", rule_type="min_order_value", rule_config={}, severity="block")
    return CommercialRule(**{**base, **over})
