"""Turn a chart spec into points read from query rows.

The model names a query, a chart type, and two columns. This module drops anything
that does not match those rows. Bar and line charts keep row order and do not
aggregate. A pie chart sums one category into at most six non-negative slices.
"""

from __future__ import annotations

from typing import Any

from .analyze import value_kind

CHART_TYPES = frozenset({"bar", "line", "pie"})
MAX_CHARTS = 3
MAX_POINTS = 12
MAX_SLICES = 6
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


def suggest_specs(series: dict[str, list[dict[str, Any]]], *, chinese: bool = False) -> list[dict[str, Any]]:
    """Charts the model left out, when a series has a category or a time and a number.

    A single total has neither axis, so it stays a sentence. A repeated category with
    a few labels becomes a pie, because a bar would plot the same label once per row.
    """
    specs: list[dict[str, Any]] = []
    for query_id, rows in series.items():
        if len(specs) >= MAX_CHARTS or len(rows) < 2:
            continue
        kinds = {item["name"]: item["kind"] for item in column_kinds(rows)}
        y_name = next((name for name, kind in kinds.items() if kind == "number" and not _id_like(name)), None)
        if y_name is None:
            continue
        axes: list[tuple[str, str]] = []
        times = [name for name, kind in kinds.items() if kind == "time"]
        if times:
            axes.append((times[0], "line"))
        texts = sorted(
            (name for name, kind in kinds.items() if kind == "text"),
            key=lambda name: _distinct(rows, name),
        )
        for name in texts:
            distinct = _distinct(rows, name)
            if distinct < 2:
                continue
            chart_type = "pie" if 2 <= distinct <= MAX_SLICES and _non_negative(rows, y_name) else "bar"
            axes.append((name, chart_type))
            if len(axes) >= 2:
                break
        for x_name, chart_type in axes:
            if len(specs) >= MAX_CHARTS:
                break
            title = f"{y_name} 按 {x_name}" if chinese else f"{y_name} by {x_name}"
            specs.append({
                "query_id": query_id, "chart_type": chart_type, "x": x_name, "y": y_name,
                "title": title[:_TITLE],
            })
    return specs


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
        kinds.append({"name": name, "kind": value_kind(name, values)})
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
    points = _pie_points(rows, x_key, y_key) if chart_type == "pie" else _series_points(rows, x_key, y_key)
    if not points:
        return None
    title = str(spec.get("title") or "").strip()[:_TITLE] or f"{y_key} by {x_key}"[:_TITLE]
    return {
        "query_id": query_id, "chart_type": chart_type, "x_key": x_key, "y_key": y_key,
        "title": title, "points": points,
    }


def _series_points(rows: list[dict[str, Any]], x_key: str, y_key: str) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for row in rows:
        if len(points) >= MAX_POINTS:
            break
        y = row.get(y_key)
        label = _label(row.get(x_key))
        if not _is_number(y) or not label:
            continue
        points.append({"x": label, "y": y})
    return points


def _pie_points(rows: list[dict[str, Any]], x_key: str, y_key: str) -> list[dict[str, Any]]:
    """Sum one label into one slice. Drop the chart when a value is negative or the mix is not 2-6 slices."""
    grouped: dict[str, int | float] = {}
    order: list[str] = []
    for row in rows:
        y = row.get(y_key)
        label = _label(row.get(x_key))
        if not label or not _is_number(y):
            continue
        if y < 0:
            return []
        if label not in grouped:
            order.append(label)
            grouped[label] = y
        else:
            grouped[label] = grouped[label] + y
    if not 2 <= len(order) <= MAX_SLICES or sum(grouped.values()) <= 0:
        return []
    return [{"x": label, "y": grouped[label]} for label in order]


def _column(rows: list[dict[str, Any]], name: str) -> bool:
    return any(name in row for row in rows)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value


def _id_like(name: str) -> bool:
    folded = name.casefold()
    return folded == "id" or folded.endswith("_id")


def _distinct(rows: list[dict[str, Any]], name: str) -> int:
    labels = {_label(row.get(name)) for row in rows}
    labels.discard("")
    return len(labels)


def _non_negative(rows: list[dict[str, Any]], name: str) -> bool:
    values = [row.get(name) for row in rows if _is_number(row.get(name))]
    return bool(values) and all(value >= 0 for value in values)


def _label(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, float) and value == value and value.is_integer():
        text = str(int(value))
    else:
        text = str(value).strip()
    return text[:_LABEL]
