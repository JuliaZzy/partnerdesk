"""Numbers derived from query rows. The model quotes these; it does not recompute them.

One finding per numeric column. A time column plus a number is a trend (first period,
last period, change). A short text column plus a number is a mix (sum and share).
A number with neither is a range (min, max, mean). A single value is left in the row.
Shares are rounded to one decimal. The mean is rounded to two.
"""

from __future__ import annotations

import re
from typing import Any

MAX_GROUPS = 12
MAX_METRICS = 2
_TIME_TOKENS = frozenset({"year", "month", "quarter", "week", "date", "period", "day"})
_DATE = re.compile(r"^\d{4}-\d{2}(-\d{2})?([T ].*)?$")


def findings(query_id: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Trend, mix, or range for up to two numeric columns. Nothing when the rows cannot support one."""
    if not rows:
        return []
    names = _names(rows)
    numeric = [name for name in names if value_kind(name, _values(rows, name)) == "number" and not _id_like(name)]
    time_name = next((name for name in names if value_kind(name, _values(rows, name)) == "time"), None)
    text_name = _mix_column(rows, [name for name in names if value_kind(name, _values(rows, name)) == "text"])
    found: list[dict[str, Any]] = []
    for name in numeric[:MAX_METRICS]:
        emitted = False
        if time_name is not None:
            trend = _trend(query_id, rows, time_name, name)
            if trend is not None:
                found.append(trend)
                emitted = True
        if text_name is not None:
            mix = _mix(query_id, rows, text_name, name)
            if mix is not None:
                found.append(mix)
                emitted = True
        if emitted:
            continue
        span = _range(query_id, rows, name)
        if span is not None:
            found.append(span)
    return found


def value_kind(name: str, values: list[Any]) -> str:
    """``time``, ``number``, or ``text``. A year column stays time even though its cells are integers."""
    present = [value for value in values if value is not None and value != ""]
    if not present:
        return "text"
    if _is_time_name(name) or all(_is_date(value) for value in present):
        return "time"
    if all(_is_number(value) for value in present):
        return "number"
    return "text"


def _names(rows: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for row in rows:
        for key in row:
            if key not in names:
                names.append(str(key))
    return names


def _values(rows: list[dict[str, Any]], name: str) -> list[Any]:
    return [row.get(name) for row in rows if row.get(name) is not None]


def _id_like(name: str) -> bool:
    folded = name.casefold()
    return folded == "id" or folded.endswith("_id")


def _is_time_name(name: str) -> bool:
    tokens = set(name.casefold().replace("-", "_").split("_"))
    return bool(tokens & _TIME_TOKENS)


def _is_date(value: Any) -> bool:
    return isinstance(value, str) and bool(_DATE.match(value.strip()))


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value


def _label(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, float) and value == value and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _number(value: Any) -> int | float | None:
    if not _is_number(value):
        return None
    return value


def _mix_column(rows: list[dict[str, Any]], names: list[str]) -> str | None:
    """The text column with the fewest distinct labels, when at least two labels repeat or fit on one chart."""
    best: str | None = None
    best_count: int | None = None
    for name in names:
        labels = [_label(row.get(name)) for row in rows]
        labels = [label for label in labels if label]
        distinct = len(set(labels))
        if distinct < 2:
            continue
        if distinct == len(labels) and distinct > MAX_GROUPS:
            continue
        if best_count is None or distinct < best_count:
            best, best_count = name, distinct
    return best


def _buckets(rows: list[dict[str, Any]], x_name: str, y_name: str) -> list[tuple[str, int | float]]:
    grouped: dict[str, int | float] = {}
    order: list[str] = []
    for row in rows:
        label = _label(row.get(x_name))
        value = _number(row.get(y_name))
        if not label or value is None:
            continue
        if label not in grouped:
            order.append(label)
            grouped[label] = value
        else:
            grouped[label] = grouped[label] + value
    return [(label, grouped[label]) for label in order]


def _trend(query_id: str, rows: list[dict[str, Any]], x_name: str, y_name: str) -> dict[str, Any] | None:
    pairs = sorted(_buckets(rows, x_name, y_name), key=lambda item: item[0])
    if len(pairs) < 2:
        return None
    start_label, start_value = pairs[0]
    end_label, end_value = pairs[-1]
    change = end_value - start_value
    found: dict[str, Any] = {
        "id": query_id, "kind": "trend", "x": x_name, "y": y_name,
        "start": {"label": start_label, "value": start_value},
        "end": {"label": end_label, "value": end_value},
        "change": change,
        "direction": "up" if change > 0 else "down" if change < 0 else "flat",
    }
    if start_value != 0:
        found["change_percent"] = round(change / start_value * 100, 1)
    return found


def _mix(query_id: str, rows: list[dict[str, Any]], x_name: str, y_name: str) -> dict[str, Any] | None:
    pairs = _buckets(rows, x_name, y_name)
    if len(pairs) < 2:
        return None
    total = sum(value for _, value in pairs)
    shares = total > 0 and all(value >= 0 for _, value in pairs)
    groups = []
    for label, value in sorted(pairs, key=lambda item: (-item[1], item[0])):
        group: dict[str, Any] = {"label": label, "value": value}
        if shares:
            group["share_percent"] = round(value / total * 100, 1)
        groups.append(group)
    return {
        "id": query_id, "kind": "mix", "x": x_name, "y": y_name,
        "total": total, "group_count": len(groups),
        "top": groups[0], "bottom": min(groups, key=lambda group: (group["value"], group["label"])),
        "groups": groups[:MAX_GROUPS],
    }


def _range(query_id: str, rows: list[dict[str, Any]], y_name: str) -> dict[str, Any] | None:
    values = [value for value in (_number(row.get(y_name)) for row in rows) if value is not None]
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    shown: int | float = int(mean) if mean == int(mean) else round(mean, 2)
    return {
        "id": query_id, "kind": "range", "y": y_name,
        "count": len(values), "min": min(values), "max": max(values), "mean": shown,
    }
