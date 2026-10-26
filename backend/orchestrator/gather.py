"""Run every planned task together, then one reply writes the session.

Results are stored in Redis as each task finishes. The reply waits until the
counter hits zero, so arrival order does not matter. A single agent with no
other task stays on that agent's own path in `turn.py`.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from ..brand_knowledge.answer import _search_query, _with_sources
from ..brand_knowledge.rag import RagUnavailable, retrieve
from ..contract.turn import file_contract
from ..db import Database
from ..llm import LLM
from ..memory import prompt_context, standing_block, summary_block
from ..po.agent import collect_order
from ..po.turn import Bundle
from ..redis_store import begin_turn, drop_turn, load_results, store_result
from ..report.turn import ReportDraft, iter_draft
from ..text2sql import answer_question
from ..tools import all_tools
from .prompt import SYNTHESIZE
from .speak import answer_directly
from .status import status

log = logging.getLogger(__name__)

Emit = Callable[[dict[str, Any]], None]


def scheduled(tasks: list[Any]) -> list[Any]:
    """Drop a data task a report lists as its own input. The draft queries itself."""
    hinted: set[str] = set()
    for task in tasks:
        if getattr(task, "kind", "") == "agent" and task.name == "report":
            hinted.update(task.depends_on)
    if not hinted:
        return list(tasks)
    return [
        task for task in tasks
        if not (task.kind == "agent" and task.name == "data" and task.id in hinted)
    ]


def fan_out(
    db: Database, llm: LLM, session: dict[str, Any], text: str, *,
    images: list[str], documents: list[tuple[str, bytes]], bundle: Bundle,
    tasks: list[Any], work: list[Any],
) -> Iterator[dict[str, Any]]:
    """Run `work` in dependency waves. Yield status as each task moves, then one reply."""
    session_id = str(session["id"])
    turn_id = uuid.uuid4().hex
    ids = [task.id for task in work]
    begin_turn(session_id, turn_id, ids)
    sink: queue.Queue[tuple[str, Any]] = queue.Queue()
    snapshot = dict(session)
    finished_results: dict[str, dict[str, Any]] = {}
    guard = threading.Lock()

    def run(task: Any) -> None:
        def emit(event: dict[str, Any]) -> None:
            sink.put(("event", event))

        with guard:
            prior = [finished_results[dep] for dep in task.depends_on if dep in finished_results]
        try:
            payload = _execute(
                task, db=db, llm=llm, session=snapshot, text=text, images=images,
                documents=documents, bundle=bundle, tasks=tasks, emit=emit, prior=prior,
            )
            result = {
                "id": task.id, "kind": task.kind, "name": task.name,
                "ok": True, "error": None, "payload": payload,
            }
        except Exception as err:
            log.exception("task %s failed", task.name)
            result = {
                "id": task.id, "kind": task.kind, "name": task.name,
                "ok": False, "error": str(err), "payload": {},
            }
        try:
            store_result(session_id, turn_id, task.id, result)
        except Exception:
            log.exception("could not store task %s", task.name)
        with guard:
            finished_results[task.id] = result
        sink.put(("done", (task.id, _done_line(task, result))))

    known = set(ids)
    finished: set[str] = set()
    pending = list(work)
    lines = {task.id: _running_line(task) for task in work}
    try:
        while pending:
            ready = [
                task for task in pending
                if all(dep not in known or dep in finished for dep in task.depends_on)
            ]
            if not ready:
                ready = list(pending)
            # Show every task in this wave before any of them returns.
            for task in ready:
                yield status(f"task-{task.id}", lines[task.id], "", state="running")
            with ThreadPoolExecutor(max_workers=min(8, len(ready))) as pool:
                futures = [pool.submit(run, task) for task in ready]
                got = 0
                while got < len(ready):
                    try:
                        kind, item = sink.get(timeout=300)
                    except queue.Empty as err:
                        raise TimeoutError("a parallel task did not finish") from err
                    if kind == "event":
                        yield _relay(item)
                        continue
                    got += 1
                    task_id, detail = item
                    finished.add(task_id)
                    yield status(f"task-{task_id}", lines[task_id], detail, state="done")
                for future in futures:
                    future.result()
            pending = [task for task in pending if task.id not in finished]
        stored = load_results(session_id, turn_id)
    finally:
        drop_turn(session_id, turn_id)

    ordered = [stored[task.id] for task in work if task.id in stored]
    for result in ordered:
        yield from _side_events(result)
    extra, fields = _assemble(ordered)
    prompt = _prompt(text, ordered)
    done = yield from answer_directly(
        db, llm, session, text, prompt_text=prompt, system=SYNTHESIZE, extra=extra,
        done_step=("answer", "回答", "回复已写完"), session_fields=fields or None,
    )
    yield done


_AGENT_LINES = {
    "po": "正在整理采购单",
    "contract": "正在解析合同",
    "report": "正在写报告",
    "data": "正在查数",
    "brand": "正在检索品牌资料",
}

_TOOL_LINES = {
    "web_search": "正在进行网络搜索",
    "translate": "正在翻译",
    "exchange_rate": "正在查询汇率",
    "send_email": "正在发信",
}


def _running_line(task: Any) -> str:
    if task.kind == "tool":
        return _TOOL_LINES.get(task.name, f"正在调用{task.name}")
    return _AGENT_LINES.get(task.name, f"正在执行{task.name}")


def _done_line(task: Any, result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return str(result.get("error") or "失败")[:80]
    payload = result.get("payload") or {}
    if task.name == "contract":
        return {
            "ready": "条款已抽出，等确认",
            "need_file": "还没有协议文件",
            "failed": "读取失败",
        }.get(str(payload.get("status") or ""), "已完成")
    if task.name == "data":
        return f"{payload.get('row_count') or 0} 行" if payload.get("ok") else "没查到"
    if task.name == "brand":
        if payload.get("error"):
            return "检索不可用"
        return f"找到 {len(payload.get('hits') or [])} 段"
    if task.name == "report":
        return "草稿已好"
    if task.name == "po":
        if payload.get("po_event"):
            return "已记到订单上"
        return "可以确认" if payload.get("stage") == "confirm" else "草稿已更新"
    if task.kind == "tool":
        inner = payload.get("result") or {}
        if inner.get("ok"):
            return "已返回"
        return str(inner.get("error") or "失败")[:80]
    return "已完成"


def _relay(event: dict[str, Any]) -> dict[str, Any]:
    if event.get("type") == "status":
        return status(
            str(event.get("id") or "task"), str(event.get("title") or ""),
            str(event.get("detail") or ""), state=str(event.get("state") or "running"),
        )
    return event


def _question(task: Any) -> str | None:
    raw = task.input.get("question") or task.input.get("text") or ""
    if isinstance(raw, str) and raw.strip():
        return raw.strip()[:2000]
    return None


def _report_hint(task: Any, tasks: list[Any]) -> str | None:
    for dep in task.depends_on:
        src = next((item for item in tasks if item.id == dep and item.name == "data"), None)
        if src and _question(src):
            return _question(src)
    for item in tasks:
        if item is not task and item.name == "data" and _question(item):
            return _question(item)
    return _question(task)


def _execute(
    task: Any, *, db: Database, llm: LLM, session: dict[str, Any], text: str,
    images: list[str], documents: list[tuple[str, bytes]], bundle: Bundle,
    tasks: list[Any], emit: Emit, prior: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if task.kind == "tool":
        return _tool(task, llm, session, emit, prior or [])
    name = task.name
    if name == "po":
        return collect_order(db, llm, session, text, images, bundle, emit)
    if name == "contract":
        filed = file_contract(db, llm, session, documents)
        return {
            "status": filed.get("status"), "table": filed.get("table"),
            "pending": filed.get("pending"), "error": filed.get("error"),
        }
    if name == "report":
        return _report(db, llm, session, text, bundle, _report_hint(task, tasks), emit)
    if name == "data":
        return _data(db, llm, session, text, bundle, _question(task) or text, emit)
    if name == "brand":
        return _brand(db, llm, session, text, bundle.partnership.brand_id, emit)
    raise RuntimeError(f"unknown task {name}")


def _tool(
    task: Any, llm: LLM, session: dict[str, Any], emit: Emit, prior: list[dict[str, Any]],
) -> dict[str, Any]:
    del emit
    fn = all_tools().get(task.name)
    if fn is None:
        return {"tool": task.name, "result": {"ok": False, "data": None, "error": f"unknown tool {task.name}"}}
    try:
        result = fn(
            _arguments_for(task, prior), llm=llm, context={"history": session.get("history") or []},
        )
    except Exception as err:
        result = {"ok": False, "data": None, "error": str(err)}
    return {"tool": task.name, "result": result}


def _arguments_for(task: Any, prior: list[dict[str, Any]]) -> dict[str, Any]:
    """Planned input is a hint. A later step's body comes from the tasks it waits on."""
    args = dict(task.input or {})
    if not prior:
        return args
    if task.name == "translate":
        blob = _upstream_text(prior)
        if blob:
            args["text"] = blob
    elif task.name == "send_email":
        translated = _upstream_translation(prior)
        if translated:
            args["body"] = translated
    return args


def _inner_data(result: dict[str, Any]) -> dict[str, Any]:
    payload = result.get("payload") or {}
    inner = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    data = inner.get("data") if isinstance(inner, dict) else None
    return data if isinstance(data, dict) else {}


def _upstream_translation(prior: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for result in prior:
        if result.get("name") != "translate" or not result.get("ok"):
            continue
        text = str(_inner_data(result).get("translation") or "").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _upstream_text(prior: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    translated = _upstream_translation(prior)
    if translated:
        parts.append(translated)
    for result in prior:
        if result.get("name") == "translate" or not result.get("ok"):
            continue
        data = _inner_data(result)
        pages = data.get("results")
        if isinstance(pages, list):
            lines: list[str] = []
            for page in pages:
                if not isinstance(page, dict):
                    continue
                title = str(page.get("title") or "").strip()
                snippet = str(page.get("snippet") or "").strip()
                line = "\n".join(bit for bit in (title, snippet) if bit)
                if line:
                    lines.append(line)
            if lines:
                parts.append("\n\n".join(lines))
            continue
        text = data.get("text") or data.get("translation")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts).strip()[:6000]


def _report(
    db: Database, llm: LLM, session: dict[str, Any], text: str, bundle: Bundle,
    hint: str | None, emit: Emit,
) -> dict[str, Any]:
    draft: ReportDraft | None = None
    partnership = bundle.partnership
    for item in iter_draft(
        db, llm, message=text, history=list(session.get("history") or []),
        partnership_id=partnership.id, brand_id=partnership.brand_id,
        distributor_id=partnership.distributor_id, session_id=session["id"], hint=hint,
        folded_through=int(session.get("folded_through") or 0), summary=str(session.get("summary") or ""),
    ):
        if isinstance(item, ReportDraft):
            draft = item
        else:
            emit(item)
    if draft is None:
        draft = ReportDraft(
            text="The report draft is empty.", charts=[], web_notes=[], warnings=["draft failed"], data_query=None,
        )
    for warning in draft.warnings:
        emit({"type": "warning", "message": warning})
    return {
        "text": draft.text, "charts": draft.charts, "web_notes": draft.web_notes,
        "series": draft.series, "data_query": draft.data_query, "title": draft.title,
        "report_pending_id": f"rp_{uuid.uuid4().hex[:12]}",
    }


def _data(
    db: Database, llm: LLM, session: dict[str, Any], text: str, bundle: Bundle, question: str, emit: Emit,
) -> dict[str, Any]:
    partnership = bundle.partnership
    result = answer_question(
        db, llm, question=question, partnership_id=partnership.id, brand_id=partnership.brand_id,
        distributor_id=partnership.distributor_id, session_id=session["id"],
        history=list(session.get("history") or []),
        folded_through=int(session.get("folded_through") or 0), summary=str(session.get("summary") or ""),
    )
    if result.handoff:
        emit({"type": "warning", "message": result.handoff})
    return {
        "ok": result.ok, "row_count": result.row_count, "rows": result.rows, "error": result.error,
        "log_id": result.log_id, "question": question,
    }


def _brand(
    db: Database, llm: LLM, session: dict[str, Any], text: str, brand_id: str, emit: Emit,
) -> dict[str, Any]:
    folded = int(session.get("folded_through") or 0)
    remembered = prompt_context(
        standing_block(db, session["partnership_id"], session.get("user_id")), summary_block(session.get("summary")),
    )
    query = _search_query(llm, text, list(session.get("history") or []), remembered, folded)
    try:
        hits = retrieve(query, brand_id)
    except RagUnavailable as err:
        return {"hits": [], "error": str(err), "query": query}
    named = _with_sources(db, hits)
    return {"hits": named, "query": query, "error": None}


def _side_events(result: dict[str, Any]) -> Iterator[dict[str, Any]]:
    payload = result.get("payload") or {}
    if result.get("name") == "data":
        yield {
            "type": "data", "ok": bool(payload.get("ok")),
            "row_count": payload.get("row_count") or 0, "log_id": payload.get("log_id"),
        }
    if result.get("name") == "contract":
        yield {
            "type": "contract", "status": payload.get("status"),
            "contract_id": None, "table": payload.get("table"),
        }


def _assemble(results: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    extra: dict[str, Any] = {"combined": True}
    fields: dict[str, Any] = {}
    for result in results:
        name = result.get("name")
        payload = result.get("payload") or {}
        if name == "po" and "slots" in payload:
            expires = payload.get("confirm_expires_at")
            when = datetime.fromisoformat(expires) if isinstance(expires, str) and expires else None
            fields.update({
                "slots": payload.get("slots") or {},
                "stage": payload.get("stage") or "gathering",
                "specialist": "po",
                "confirm_token": payload.get("confirm_token"),
                "confirm_hash": payload.get("confirm_hash"),
                "confirm_expires_at": when,
            })
            if payload.get("clear_po"):
                fields["po_id"] = None
            extra.update({
                "draft": payload.get("draft"), "order_draft": payload.get("draft"), "order": True,
                "stage": payload.get("stage"), "slots": payload.get("slots"),
                "gaps": payload.get("gaps") or [], "gaps_detail": payload.get("gaps_detail") or [],
                "advisories": payload.get("advisories") or [],
                "confirm_token": payload.get("confirm_token"),
            })
        if name == "po" and payload.get("po_event"):
            extra["po_event"] = payload["po_event"]
        if name == "contract" and isinstance(payload.get("table"), dict):
            extra["contract"] = payload["table"]
            if isinstance(payload.get("pending"), dict):
                extra["contract_pending"] = payload["pending"]
        if name == "report":
            extra["report_draft"] = True
            extra["report_pending_id"] = payload.get("report_pending_id")
            extra["report_body"] = payload.get("text") or ""
            if isinstance(payload.get("title"), str) and payload["title"].strip():
                extra["report_title"] = payload["title"].strip()
            if payload.get("charts"):
                extra["charts"] = payload["charts"]
            if payload.get("series"):
                extra["report_series"] = payload["series"]
            if payload.get("web_notes"):
                extra["web_notes"] = payload["web_notes"]
            if payload.get("data_query") and "data_query" not in extra:
                extra["data_query"] = payload["data_query"]
        if name == "data" and payload.get("log_id"):
            extra["data_query"] = {
                "ok": bool(payload.get("ok")), "row_count": payload.get("row_count") or 0,
                "error": payload.get("error"), "log_id": payload["log_id"],
            }
        hits = payload.get("hits") if name == "brand" else None
        if isinstance(hits, list) and hits:
            extra["brand_hits"] = [
                {
                    "n": hit.get("n"), "cid": hit.get("cid"), "doc": hit.get("doc"), "page": hit.get("page"),
                    "file_name": hit.get("file_name"), "source": hit.get("source"),
                }
                for hit in hits if isinstance(hit, dict)
            ]
    return extra, fields


def _public(result: dict[str, Any]) -> dict[str, Any]:
    payload = dict(result.get("payload") or {})
    for key in ("contract_pending", "slots", "confirm_token", "confirm_hash", "confirm_expires_at"):
        payload.pop(key, None)
    text = payload.get("text")
    if isinstance(text, str) and len(text) > 4000:
        payload["text"] = text[:4000] + "…"
    rows = payload.get("rows")
    if isinstance(rows, list) and len(rows) > 20:
        payload["rows"] = rows[:20]
    hits = payload.get("hits")
    if isinstance(hits, list):
        payload["hits"] = [_clip_hit(hit) for hit in hits[:8] if isinstance(hit, dict)]
    return {
        "name": result.get("name"), "ok": result.get("ok"), "error": result.get("error"), "result": payload,
    }


def _clip_hit(hit: dict[str, Any]) -> dict[str, Any]:
    snippet = hit.get("text") or hit.get("content") or hit.get("chunk") or ""
    return {"n": hit.get("n"), "source": hit.get("source"), "text": str(snippet)[:500]}


def _prompt(text: str, results: list[dict[str, Any]]) -> str:
    body = json.dumps([_public(result) for result in results], ensure_ascii=False, default=str)
    if len(body) > 12000:
        body = body[:12000] + "…"
    return f"{text}\n\n=== 这一轮的结果（引用这些内容，不要重算） ===\n{body}"
