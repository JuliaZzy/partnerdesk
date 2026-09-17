"""SQLite persistence. Reference data (products, rules, partnerships, settings) is stored
as JSON documents keyed by id and seeded from `fixtures/`; transactional data (sessions,
purchase orders) has real columns where a constraint or a conditional UPDATE matters —
the confirm-token burn in particular depends on `UPDATE … WHERE confirm_token = ?`.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import DB_PATH, FIXTURES_DIR
from .models import AgentSettings, CommercialRule, Partnership, Product, PurchaseOrder

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY (kind, id)
);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  partnership_id TEXT NOT NULL,
  specialist TEXT,
  stage TEXT NOT NULL DEFAULT 'gathering',
  slots TEXT NOT NULL DEFAULT '{}',
  history TEXT NOT NULL DEFAULT '[]',
  confirm_token TEXT,
  confirm_hash TEXT,
  confirm_expires_at TEXT,
  confirmed_at TEXT,
  po_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS purchase_orders (
  id TEXT PRIMARY KEY,
  po_number TEXT NOT NULL UNIQUE,
  brand_id TEXT NOT NULL, distributor_id TEXT NOT NULL, partnership_id TEXT NOT NULL,
  status TEXT NOT NULL, currency TEXT NOT NULL,
  payment_terms TEXT NOT NULL, incoterms TEXT NOT NULL, shipping_method TEXT NOT NULL,
  eta_date TEXT, ship_to_country TEXT, notes TEXT,
  discount_amount REAL, discount_percentage REAL,
  subtotal_amount REAL NOT NULL, total_amount REAL NOT NULL,
  submitted_at TEXT, submit_cycle_version INTEGER NOT NULL DEFAULT 0,
  prepaid_amount REAL NOT NULL DEFAULT 0, balance_paid REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS purchase_order_items (
  po_id TEXT NOT NULL, product_id TEXT NOT NULL, sku TEXT NOT NULL, product_name TEXT NOT NULL,
  quantity INTEGER NOT NULL, case_price REAL NOT NULL, line_total REAL NOT NULL, case_pack INTEGER
);
CREATE TABLE IF NOT EXISTS po_rule_evaluations (
  po_id TEXT NOT NULL, evaluation_context TEXT NOT NULL, submit_cycle_version INTEGER NOT NULL,
  snapshot TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS po_events (
  po_id TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL
);
"""

_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | str = DB_PATH):
        self.path = str(path)
        with self.connect() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            yield conn
        finally:
            conn.close()

    # --- reference documents --------------------------------------------------
    def put_document(self, kind: str, id: str, data: dict[str, Any]) -> None:
        with self.connect() as c:
            c.execute(
                "INSERT INTO documents(kind,id,data) VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data",
                (kind, id, json.dumps(data, ensure_ascii=False)),
            )

    def documents(self, kind: str) -> list[dict[str, Any]]:
        with self.connect() as c:
            return [json.loads(r["data"]) for r in c.execute("SELECT data FROM documents WHERE kind=? ORDER BY id", (kind,))]

    def document(self, kind: str, id: str) -> dict[str, Any] | None:
        with self.connect() as c:
            r = c.execute("SELECT data FROM documents WHERE kind=? AND id=?", (kind, id)).fetchone()
            return json.loads(r["data"]) if r else None

    def products(self, brand_id: str) -> list[Product]:
        return [Product(**d) for d in self.documents("product") if d.get("brand_id") == brand_id]

    def rules(self, brand_id: str) -> list[CommercialRule]:
        return [CommercialRule(**d) for d in self.documents("rule") if d.get("brand_id") == brand_id]

    def partnership(self, id: str) -> Partnership | None:
        d = self.document("partnership", id)
        return Partnership(**d) if d else None

    def settings(self, brand_id: str) -> AgentSettings:
        d = self.document("settings", brand_id)
        return AgentSettings(**{k: v for k, v in (d or {}).items() if v is not None})

    def seed_from_fixtures(self, fixtures_dir: Path = FIXTURES_DIR) -> None:
        for kind, filename in (("product", "products.json"), ("rule", "rules.json"), ("partnership", "partnerships.json"), ("settings", "settings.json")):
            path = fixtures_dir / filename
            if not path.is_file():
                continue
            for doc in json.loads(path.read_text(encoding="utf-8")):
                self.put_document(kind, str(doc["id"]), doc)

    # --- sessions ---------------------------------------------------------------
    def create_session(self, id: str, partnership_id: str) -> dict[str, Any]:
        ts = now_iso()
        with self.connect() as c:
            c.execute(
                "INSERT INTO sessions(id,partnership_id,slots,history,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (id, partnership_id, "{}", "[]", ts, ts),
            )
        return self.session(id)  # type: ignore[return-value]

    def session(self, id: str) -> dict[str, Any] | None:
        with self.connect() as c:
            r = c.execute("SELECT * FROM sessions WHERE id=?", (id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["slots"] = json.loads(d["slots"])
        d["history"] = json.loads(d["history"])
        return d

    def update_session(self, id: str, **fields: Any) -> None:
        if "slots" in fields:
            fields["slots"] = json.dumps(fields["slots"], ensure_ascii=False)
        if "history" in fields:
            fields["history"] = json.dumps(fields["history"], ensure_ascii=False)
        fields["updated_at"] = now_iso()
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.connect() as c:
            c.execute(f"UPDATE sessions SET {sets} WHERE id=?", (*fields.values(), id))

    def burn_confirm_token(self, token: str) -> str | None:
        """Atomically consume a confirm token. Returns the session id, or None when the
        token was already used — two clicks landing together see one UPDATE match and one
        match nothing. Done BEFORE the order is created, never after."""
        with _lock, self.connect() as c:
            r = c.execute(
                "UPDATE sessions SET confirm_token=NULL, confirmed_at=?, updated_at=? WHERE confirm_token=? RETURNING id",
                (now_iso(), now_iso(), token),
            ).fetchone()
            return r["id"] if r else None

    def session_by_token(self, token: str) -> dict[str, Any] | None:
        with self.connect() as c:
            r = c.execute("SELECT id FROM sessions WHERE confirm_token=?", (token,)).fetchone()
        return self.session(r["id"]) if r else None

    # --- purchase orders --------------------------------------------------------
    def insert_po(self, po: PurchaseOrder, items: list[dict[str, Any]], rule_snapshot: list[dict[str, Any]]) -> None:
        ts = now_iso()
        with self.connect() as c:
            c.execute("BEGIN")
            c.execute(
                """INSERT INTO purchase_orders(id,po_number,brand_id,distributor_id,partnership_id,status,currency,
                   payment_terms,incoterms,shipping_method,eta_date,ship_to_country,notes,discount_amount,discount_percentage,
                   subtotal_amount,total_amount,submitted_at,submit_cycle_version,prepaid_amount,balance_paid,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (po.id, po.po_number, po.brand_id, po.distributor_id, po.partnership_id, po.status, po.currency,
                 po.payment_terms, po.incoterms, po.shipping_method, po.eta_date, po.ship_to_country, po.notes,
                 po.discount_amount, po.discount_percentage, po.subtotal_amount, po.total_amount, po.submitted_at,
                 po.submit_cycle_version, po.prepaid_amount, po.balance_paid, ts, ts),
            )
            c.executemany(
                "INSERT INTO purchase_order_items(po_id,product_id,sku,product_name,quantity,case_price,line_total,case_pack) VALUES(?,?,?,?,?,?,?,?)",
                [(po.id, i["product_id"], i["sku"], i["product_name"], i["quantity"], i["case_price"], i["line_total"], i["case_pack"]) for i in items],
            )
            if rule_snapshot:
                c.execute(
                    "INSERT INTO po_rule_evaluations(po_id,evaluation_context,submit_cycle_version,snapshot,created_at) VALUES(?,?,?,?,?)",
                    (po.id, "submission", po.submit_cycle_version, json.dumps(rule_snapshot, ensure_ascii=False), ts),
                )
            c.execute("COMMIT")

    def po(self, id: str) -> PurchaseOrder | None:
        with self.connect() as c:
            r = c.execute("SELECT * FROM purchase_orders WHERE id=?", (id,)).fetchone()
        return PurchaseOrder(**dict(r)) if r else None

    def po_items(self, id: str) -> list[dict[str, Any]]:
        with self.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM purchase_order_items WHERE po_id=?", (id,))]

    def list_pos(self, partnership_id: str | None = None) -> list[PurchaseOrder]:
        with self.connect() as c:
            q = "SELECT * FROM purchase_orders" + (" WHERE partnership_id=?" if partnership_id else "") + " ORDER BY created_at DESC"
            rows = c.execute(q, (partnership_id,) if partnership_id else ()).fetchall()
        return [PurchaseOrder(**dict(r)) for r in rows]

    def update_po(self, id: str, **fields: Any) -> None:
        fields["updated_at"] = now_iso()
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.connect() as c:
            c.execute(f"UPDATE purchase_orders SET {sets} WHERE id=?", (*fields.values(), id))

    def add_po_event(self, po_id: str, kind: str, data: dict[str, Any]) -> None:
        with self.connect() as c:
            c.execute("INSERT INTO po_events(po_id,kind,data,created_at) VALUES(?,?,?,?)", (po_id, kind, json.dumps(data, ensure_ascii=False), now_iso()))

    def po_events(self, po_id: str) -> list[dict[str, Any]]:
        with self.connect() as c:
            return [{"kind": r["kind"], "data": json.loads(r["data"]), "created_at": r["created_at"]} for r in c.execute("SELECT * FROM po_events WHERE po_id=? ORDER BY rowid", (po_id,))]
