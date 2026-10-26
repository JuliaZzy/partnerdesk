"""Save a chat report the user has edited.

The draft stays on the session until this runs. A second confirm updates the same row
instead of filing another write-up.
"""

from __future__ import annotations

import uuid
from typing import Any

from ..db import Database
from ..memory import record_history
from ..schemas import NarrativeReport

_BODY = 80_000


def confirm_report(
    db: Database, session_id: str, pending_id: str, *, title: str, text: str, charts: list[Any],
) -> dict[str, Any]:
    """Write the edited draft. The caller holds the session lock."""
    session = db.session(session_id)
    if not session:
        raise KeyError("session not found")
    body = text.strip()
    if not body:
        raise ValueError("The report is empty.")
    if len(body) > _BODY:
        raise ValueError("The report is too long to file.")
    history = [dict(item) if isinstance(item, dict) else item for item in (session.get("history") or [])]
    message = next((item for item in history if isinstance(item, dict) and item.get("report_pending_id") == pending_id), None)
    if message is None:
        raise KeyError("report draft not found")
    partnership = db.partnership(session["partnership_id"])
    if not partnership:
        raise KeyError("partnership not found")
    existed = isinstance(message.get("report_id"), str) and bool(message.get("report_id"))
    report_id = message["report_id"] if existed else f"nr_{uuid.uuid4().hex[:12]}"
    label = _title(title)
    saved_charts = _charts(charts)
    db.save_narrative_report(NarrativeReport(
        id=report_id, brand_id=partnership.brand_id, partnership_id=session["partnership_id"],
        session_id=session_id, title=label, body=body, charts=saved_charts,
    ))
    message["content"] = body
    message["report_title"] = label
    message["report_id"] = report_id
    message["charts"] = saved_charts
    note = (
        "Updated the report. The Reports page has the new text."
        if existed else
        "Saved the report. It is on the Reports page."
    )
    history.extend([{"role": "user", "content": "(confirm)"}, {"role": "assistant", "content": note}])
    record_history(db, session, history)
    return {"id": report_id, "title": label, "message": note}


def _title(given: str) -> str:
    """The title the draft already carries. An empty box does not borrow a section heading."""
    return (" ".join(given.split()) or "报告")[:200]


def _charts(raw: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in raw:
        if len(out) >= 4 or not isinstance(item, dict):
            continue
        option = item.get("echarts_option")
        if not isinstance(option, dict):
            option = item.get("option")
        title = str(item.get("title") or "").strip()
        if not title or not isinstance(option, dict):
            continue
        chart_id = str(item.get("id") or f"chart_{len(out) + 1:03d}").strip()[:64]
        out.append({
            "id": chart_id,
            "title": title[:200],
            "subtitle": str(item.get("subtitle") or "")[:200],
            "echarts_option": option,
        })
    return out
