"""Turn a chart spec into points read from query rows.

The model names a query, a chart type, and two columns. This module drops anything
that does not match those rows. It does not aggregate or fill in missing numbers.
"""

from __future__ import annotations

from typing import Any

CHART_TYPES = frozenset({"bar", "line"})
MAX_CHARTS = 3
MAX_POINTS = 12
_LABEL = 80
_TITLE = 80


def bind_charts(specs: Any, series: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Keep specs whose columns exist. Points are the row values, in row order."""
    if not isinstance(specs, list):
        return []
    charts: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for spec in specs:
        if len(charts) >= MAX_CHARTS or not isinstance(spec, dict):
            continue
        chart = _one(spec, series)
        if chart is None:
            continue
        key = (chart["query_id"], chart["chart_type"], chart["x_key"], chart["y_key"])
        if key in seen:
            continue
        seen.add(key)
        charts.append({
            "title": chart["title"],
            "chart_type": chart["chart_type"],
            "points": chart["points"],
        })
    return charts


def column_kinds(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Column names plus whether every present value is numeric. No cell values."""
    names: list[str] = []
    for row in rows:
        for key in row:
            if key not in names:
                names.append(str(key))
    kinds: list[dict[str, str]] = []
    for name in names[:12]:
        values = [row.get(name) for row in rows if row.get(name) is not None]
        kind = "number" if values and all(_is_number(value) for value in values) else "text"
        kinds.append({"name": name, "kind": kind})
    return kinds


def _one(spec: dict[str, Any], series: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    query_id = str(spec.get("query_id") or "").strip()
    chart_type = str(spec.get("chart_type") or "").strip().lower()
    x_key = str(spec.get("x") or "").strip()
    y_key = str(spec.get("y") or "").strip()
    rows = series.get(query_id)
    if chart_type not in CHART_TYPES or not rows or not x_key or not y_key:
        return None
    if not _column(rows, x_key) or not _column(rows, y_key):
        return None
    points: list[dict[str, Any]] = []
    for row in rows:
        if len(points) >= MAX_POINTS:
            break
        y = row.get(y_key)
        label = _label(row.get(x_key))
        if not _is_number(y) or not label:
            continue
        points.append({"x": label, "y": y})
    if not points:
        return None
    title = str(spec.get("title") or "").strip()[:_TITLE] or f"{y_key} by {x_key}"[:_TITLE]
    return {
        "query_id": query_id, "chart_type": chart_type, "x_key": x_key, "y_key": y_key,
        "title": title, "points": points,
    }


def _column(rows: list[dict[str, Any]], name: str) -> bool:
    return any(name in row for row in rows)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value


def _label(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, float) and value == value and value.is_integer():
        text = str(int(value))
    else:
        text = str(value).strip()
    return text[:_LABEL]
