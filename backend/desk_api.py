"""HTTP routes for the desk pages.

The chat stream stays in app.py. Catalog, contracts, reports and memory
used to be called in-process by the Streamlit pages; the web client only speaks HTTP,
so those operations are exposed here. Numbers and rule results still come from the
deterministic code underneath — these handlers only move bytes and records.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from . import service
from .config import chat_models, default_model_id
from .contract import contract_discount_for_next_order, ingest_extraction, read_and_ingest
from .contract.chunks import nest_chunk_rows
from .db import Database
from .llm import LLM, OpenAICompatLLM
from .memory import KIND_LABELS, memory_block, remember
from .models import PRODUCT_STATUS_VALUES, Partnership
from .report import spreadsheet as reports

_MAX_FILE_BYTES = 20 * 1024 * 1024

# pending
_MATRIX = {"units", "revenue", "stock"}


class MemoryIn(BaseModel):
    content: str
    kind: str = "fact"


class MemoryActive(BaseModel):
    active: bool


def _dump(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


def _llm_for(get_llm: Callable[[], LLM], model: str | None) -> LLM:
    llm = get_llm()
    if model and isinstance(llm, OpenAICompatLLM):
        return llm.with_model(model)
    return llm


def _matrix(raw: str | None) -> str | None:
    value = (raw or "").strip()
    if not value or value.startswith("("):
        return None
    if value not in _MATRIX:
        raise HTTPException(400, "matrix must be units, revenue or stock")
    return value


async def _upload(file: UploadFile) -> tuple[str, bytes]:
    name = file.filename or "file"
    data = await file.read()
    if len(data) > _MAX_FILE_BYTES:
        raise HTTPException(400, f"{name} is larger than 12 MB")
    if not data:
        raise HTTPException(400, "empty file")
    return name, data


def mount_desk(app: FastAPI, get_db: Callable[[], Database], get_llm: Callable[[], LLM]) -> None:
    router = APIRouter()

    def db() -> Database:
        return get_db()

    def partnership(pid: str) -> Partnership:
        found = db().partnership(pid)
        if not found:
            raise HTTPException(404, "partnership not found")
        return found

    @router.get("/api/meta")
    def meta() -> dict[str, Any]:
        return {
            "models": [
                {
                    "id": m.id, "label": m.label, "short": m.short or m.label, "description": m.description,
                    "vision": m.vision, "thinking": m.thinking,
                }
                for m in chat_models()
            ],
            "default_model": default_model_id(),
            "product_statuses": list(PRODUCT_STATUS_VALUES),
            "memory_kinds": KIND_LABELS,
            "max_images": service.MAX_IMAGES,
        }

    @router.get("/api/sessions")
    def list_sessions(partnership_id: str) -> list[dict[str, Any]]:
        partnership(partnership_id)
        rows = db().list_sessions(partnership_id)
        keep = ("id", "partnership_id", "title", "tags", "stage", "updated_at", "created_at")
        return [{k: row.get(k) for k in keep} for row in rows]

    @router.get("/api/catalog/categories")
    def catalog_categories() -> list[str]:
        return db().catalog_categories()

    @router.get("/api/catalog")
    def catalog(
        q: str | None = None, brand: str | None = None, category: str | None = None, status: str | None = None,
        page: int = 1, page_size: int = 100,
    ) -> dict[str, Any]:
        page_size = min(max(page_size, 1), 200)
        page = max(page, 1)
        store = db()
        stats = store.catalog_stats(q, brand=brand, category=category, status=status)
        items, total = store.search_catalog(
            q, brand=brand, category=category, status=status, limit=page_size, offset=(page - 1) * page_size,
        )
        return {"items": items, "total": total, "stats": stats, "page": page, "page_size": page_size}

    @router.get("/api/partnerships/{pid}/contracts")
    def contracts(pid: str) -> list[dict[str, Any]]:
        partnership(pid)
        return [_dump(c) for c in db().contracts(pid)]

    @router.get("/api/contracts/{cid}")
    def contract_detail(cid: str) -> dict[str, Any]:
        store = db()
        found = store.contract(cid)
        if not found:
            raise HTTPException(404, "contract not found")
        discount = None
        if found.status == "active":
            nxt = contract_discount_for_next_order(store, found.partnership_id)
            if nxt:
                discount = {
                    "percent": nxt.percent, "note": nxt.note, "container_index": nxt.container_index,
                    "rule_title": nxt.rule_title,
                }
        flat = [c.model_dump() for c in store.contract_chunks(cid)]
        return {
            "contract": _dump(found),
            "terms": _dump(store.contract_terms(cid)),
            "rules": [_dump(r) for r in store.derived_rules(cid)],
            "extractions": store.contract_extractions(cid),
            "outline": nest_chunk_rows(flat) if flat else [],
            "next_discount": discount,
        }

    @router.post("/api/partnerships/{pid}/contracts")
    async def import_contract(
        pid: str, file: UploadFile = File(...), title: str = Form(""), model: str | None = Form(None),
    ) -> dict[str, Any]:
        p = partnership(pid)
        name, data = await _upload(file)
        label = title.strip() or name
        try:
            if name.lower().endswith(".json"):
                parsed = json.loads(data.decode("utf-8"))
                extraction = parsed.get("extraction", parsed) if isinstance(parsed, dict) else None
                if not isinstance(extraction, dict):
                    raise ValueError("the file is not a JSON object")
                rec = ingest_extraction(
                    db(), extraction, brand_id=p.brand_id, partnership_id=p.id, title=label, file_name=name,
                    model=parsed.get("model") if isinstance(parsed, dict) else None,
                )
            else:
                rec = read_and_ingest(
                    db(), _llm_for(get_llm, model), brand_id=p.brand_id, partnership_id=p.id,
                    file_name=name, data=data, title=label,
                )
        except HTTPException:
            raise
        except Exception as err:
            raise HTTPException(400, f"Could not import: {err}") from err
        return {
            "id": rec.contract.id,
            "title": rec.contract.title or rec.contract.id,
            "discount_rules": len(rec.terms.discount_rules),
            "po_rules": len(rec.derived_rules),
            "skipped": rec.skipped,
        }

    @router.get("/api/partnerships/{pid}/reports")
    def list_reports(pid: str) -> list[dict[str, Any]]:
        partnership(pid)
        return [_dump(r) for r in db().reports(pid)]

    @router.get("/api/reports/{rid}")
    def report_detail(rid: str) -> dict[str, Any]:
        store = db()
        found = store.report(rid)
        if not found:
            raise HTTPException(404, "report not found")
        return {
            "report": _dump(found),
            "facts": [_dump(f) for f in store.report_facts(rid)],
            "extractions": store.report_extractions(rid),
        }

    def _analyze(p: Partnership, name: str, data: bytes, period: str, matrix: str | None) -> reports.Analysis:
        try:
            return reports.analyze(
                name, data, reporting_period=period.strip() or None, currency=p.currency,
                products=db().products(p.brand_id), matrix_metric=_matrix(matrix),
            )
        except HTTPException:
            raise
        except Exception as err:
            raise HTTPException(400, f"Could not read the file: {err}") from err

    def _analysis_body(analysis: reports.Analysis) -> dict[str, Any]:
        return {
            "file_name": analysis.file_name,
            "fact_count": len(analysis.facts),
            "facts": [_dump(f) for f in analysis.facts[:60]],
            "skipped": analysis.skipped,
            "mappings": [
                {"sheet": m.sheet, "header_row": m.header_row, "currency": m.currency, "columns": m.describe()}
                for m in analysis.mappings
            ],
        }

    @router.post("/api/partnerships/{pid}/reports/preview")
    async def preview_report(
        pid: str, file: UploadFile = File(...), period: str = Form(""), matrix: str = Form(""),
    ) -> dict[str, Any]:
        p = partnership(pid)
        name, data = await _upload(file)
        return _analysis_body(_analyze(p, name, data, period, matrix))

    @router.post("/api/partnerships/{pid}/reports")
    async def save_report(
        pid: str, file: UploadFile = File(...), period: str = Form(""), title: str = Form(""), matrix: str = Form(""),
    ) -> dict[str, Any]:
        p = partnership(pid)
        name, data = await _upload(file)
        analysis = _analyze(p, name, data, period, matrix)
        if not analysis.facts:
            raise HTTPException(400, "No facts could be read from this file.")
        saved = reports.ingest(db(), p, analysis, title=title.strip() or None)
        return _dump(saved)

    @router.post("/api/reports/{rid}/confirm")
    def confirm_report(rid: str) -> dict[str, int]:
        try:
            return db().confirm_report(rid)
        except KeyError as err:
            raise HTTPException(404, str(err)) from err

    @router.delete("/api/reports/{rid}", status_code=204)
    def delete_report(rid: str) -> Response:
        db().delete_report(rid)
        return Response(status_code=204)

    @router.get("/api/partnerships/{pid}/facts")
    def confirmed_facts(pid: str) -> list[dict[str, Any]]:
        partnership(pid)
        return [_dump(f) for f in db().confirmed_facts(pid)]

    @router.get("/api/partnerships/{pid}/memories")
    def memories(pid: str) -> dict[str, Any]:
        partnership(pid)
        store = db()
        active = store.memories(pid, active_only=True)
        inactive = [m for m in store.memories(pid, active_only=False) if not m.is_active]
        return {
            "active": [_dump(m) for m in active],
            "inactive": [_dump(m) for m in inactive],
            "block": memory_block(active),
        }

    @router.post("/api/partnerships/{pid}/memories")
    def add_memory(pid: str, body: MemoryIn) -> dict[str, Any]:
        partnership(pid)
        if body.kind not in KIND_LABELS:
            raise HTTPException(400, "unknown memory kind")
        try:
            saved = remember(db(), pid, body.content, kind=body.kind, source="user")
        except ValueError as err:
            raise HTTPException(400, str(err)) from err
        return _dump(saved)

    @router.patch("/api/memories/{mid}")
    def set_memory(mid: int, body: MemoryActive) -> dict[str, Any]:
        db().set_memory_active(mid, body.active)
        return {"id": mid, "active": body.active}

    @router.delete("/api/memories/{mid}", status_code=204)
    def delete_memory(mid: int) -> Response:
        db().delete_memory(mid)
        return Response(status_code=204)

    app.include_router(router)
