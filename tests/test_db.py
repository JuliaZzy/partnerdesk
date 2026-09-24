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
    assert len(db.products(p.brand_id)) == 8
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
