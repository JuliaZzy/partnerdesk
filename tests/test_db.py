"""The data layer itself: migrations are the schema, seeding is idempotent, and the demo
fixture wires a contract discount into the first order."""

from __future__ import annotations

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext

from partnerdesk.config import FIXTURES_DIR
from partnerdesk.contracts import contract_discount_for_next_order
from partnerdesk.db.orm import Base


def test_migrations_match_the_models(make_db):
    """A model change without a migration (or the reverse) fails here, not in production."""
    db = make_db("drift")
    with db.engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        assert compare_metadata(ctx, Base.metadata) == []


def test_reopening_a_database_is_a_noop(make_db):
    make_db("twice").upsert_brand("b", "Brand")
    assert make_db("twice").partnerships() == []  # second open migrates nothing, loses nothing


def test_seed_is_idempotent_and_wires_the_demo_contract(make_db):
    db = make_db("seed")
    db.seed_from_fixtures(FIXTURES_DIR)
    db.seed_from_fixtures(FIXTURES_DIR)
    (p,) = db.partnerships()
    assert p.brand_name == "Aurora Botanicals" and p.distributor_name == "Nordic Beauty Distribution"
    contract = db.active_contract(p.id)
    assert contract and contract.id == "ct-aurora-nordic-2026" and len(db.contract_extractions(contract.id)) == 1
    # Fixture rules + the two derived from the contract (min order value, discount approval), once.
    rules = db.rules(p.brand_id)
    assert len(rules) == 7 and len([r for r in rules if "(from" in r.name]) == 2
    d = contract_discount_for_next_order(db, p.id)
    assert d and d.percent == 3.0 and d.container_index == 1


def test_foreign_keys_are_enforced(make_db):
    import pytest
    from sqlalchemy.exc import IntegrityError

    from partnerdesk.models import Product

    db = make_db("fk")
    with pytest.raises(IntegrityError):
        db.upsert_product("no-such-brand", Product(id="p", sku="X"))


def test_list_sessions_is_per_partnership_newest_first(db):
    db.create_session("a", "ps-1")
    db.create_session("b", "ps-1")
    db.update_session("a", stage="confirm")  # touched last → first
    assert [r["id"] for r in db.list_sessions("ps-1")] == ["a", "b"]
    assert db.list_sessions("ps-other") == []


def test_replace_catalog_loads_searches_and_prunes(make_db):
    db = make_db("catalog")
    brands = [{"id": "b-a", "name": "Acme Foods"}, {"id": "b-z", "name": "Zeta"}]

    def prod(pid, brand, sku, en, native, status="正品"):
        return {"id": pid, "brand_id": brand, "sku": sku, "product_name_en": en, "product_name_native": native,
                "category": "Snacks", "case_pack": 12, "case_price": 24.0, "product_status": status,
                "policy_tags": ["ambient"], "restricted_territories": []}

    first = db.replace_catalog(brands, [
        prod("p-1", "b-a", "SNK-1", "Salted Crisps", "Chips salées"),
        prod("p-2", "b-a", "SNK-2", "Dark Chocolate 70%", "Chocolat noir 70%", "停产"),
        prod("p-3", "b-z", "SNK-3", "Oat Bar", "Haferriegel", "赠品"),
    ])
    assert first == {"brands": 2, "products": 3, "removed": 0}

    rows, total = db.search_catalog("chips")  # the native name, case-insensitively
    assert total == 1 and rows[0]["sku"] == "SNK-1" and rows[0]["brand_name"] == "Acme Foods"
    assert db.search_catalog("70%")[1] == 1  # LIKE wildcards in the query are literal
    assert db.search_catalog("0_")[1] == 0
    assert db.search_catalog(brand="acme")[1] == 2
    assert [r["sku"] for r in db.search_catalog(status="停产")[0]] == ["SNK-2"]
    assert db.catalog_stats() == {"by_status": {"正品": 1, "停产": 1, "赠品": 1}, "brands": 2, "categories": 1}
    page, total = db.search_catalog(limit=2, offset=2)
    assert total == 3 and [r["sku"] for r in page] == ["SNK-3"]  # ordered by brand name, then SKU

    # A reload is the whole catalog: what's missing goes, what changed is updated. Only brands
    # with a product are written, but none is deleted — Zeta keeps its row, just no products.
    second = db.replace_catalog(brands, [prod("p-1", "b-a", "SNK-1", "Sea Salt Crisps", "Chips au sel de mer")])
    assert second == {"brands": 1, "products": 1, "removed": 2}
    assert db.products("b-z") == [] and db.search_catalog(brand="zeta")[1] == 0
    assert [p.product_name_en for p in db.products("b-a")] == ["Sea Salt Crisps"]
