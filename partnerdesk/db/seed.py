"""Seed reference data from `fixtures/*.json`. Idempotent: every row is upserted by id, and
a fixture contract is ingested once (its id is fixed in the file)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..models import AgentSettings, CommercialRule, Partnership, Product

if TYPE_CHECKING:
    from .database import Database


def _load(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []


def seed_from_fixtures(db: Database, fixtures_dir: Path) -> None:
    from ..contracts import ingest_extraction  # lazy: contracts imports this package

    brands = {b["id"]: b for b in _load(fixtures_dir / "brands.json")}
    distributors = {d["id"]: d for d in _load(fixtures_dir / "distributors.json")}
    for b in brands.values():
        db.upsert_brand(b["id"], b["name"], b.get("preferred_currency") or "USD")
    for d in distributors.values():
        db.upsert_distributor(d["id"], d["name"], d.get("country"))
    for p in _load(fixtures_dir / "partnerships.json"):
        db.upsert_partnership(Partnership(
            **p,
            brand_name=p.get("brand_name") or brands.get(p["brand_id"], {}).get("name") or p["brand_id"],
            distributor_name=p.get("distributor_name") or distributors.get(p["distributor_id"], {}).get("name") or p["distributor_id"],
        ))
    for p in _load(fixtures_dir / "products.json"):
        db.upsert_product(p["brand_id"], Product(**p))
    for r in _load(fixtures_dir / "rules.json"):
        db.upsert_rule(r["brand_id"], CommercialRule(**r))
    for s in _load(fixtures_dir / "settings.json"):
        db.put_settings(s["id"], AgentSettings(**{k: v for k, v in s.items() if k != "id" and v is not None}))
    for c in _load(fixtures_dir / "contracts.json"):
        if db.contract(c["id"]):
            continue
        partnership = db.partnership(c["partnership_id"])
        if not partnership:
            continue
        ingest_extraction(
            db, c["extraction"], brand_id=partnership.brand_id, partnership_id=partnership.id, contract_id=c["id"],
            title=c.get("title"), file_name=c.get("file_name"), model=c.get("model"),
        )
