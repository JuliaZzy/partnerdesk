"""Sales reports → facts (draft) → a person confirms → the live numbers.

Scope now: TIDY tables — one header row, one row per period / SKU / channel, numeric
columns — which is what a distributor's export from their own system looks like. The
messy multi-block workbook of the first version (docs/porting/06-report-core.md: merged
cells, prose between tables, months as columns) comes later and feeds the same
`facts_from_table`: a model will LOCATE tables, this code will still READ the cells.

Nothing here is extracted by a model. The column mapping is a vocabulary match, every
number goes through `coerce_numeric`, total rows are dropped, and the facts sit in draft
until someone presses Confirm on the Reports page — the same gate an order goes through.
"""

from __future__ import annotations

import calendar
import hashlib
import io
import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from .db import Database
from .models import Partnership, Product, ReportFact, SalesReport

# --- vocabulary -----------------------------------------------------------------------
# Header → role. Longer entries are tried first so "sales qty" is units before "sales"
# is revenue. Headers are kept verbatim on every fact (`source_header`); the role is ours.
ROLE_VOCAB: dict[str, tuple[str, ...]] = {
    "sku": ("sku", "货号", "编码", "编号", "product code", "item code", "item no", "article", "产品编码", "商品编码", "条码", "barcode", "ean"),
    "product": ("product", "product name", "item", "item name", "description", "产品", "产品名称", "商品", "商品名称", "品名", "名称"),
    "channel": ("channel", "platform", "store", "retailer", "customer", "account", "渠道", "平台", "店铺", "客户"),
    "period": ("date", "month", "period", "week", "year", "日期", "月份", "月", "周", "时间", "期间"),
    "units": ("units", "unit", "qty", "quantity", "pcs", "pieces", "volume", "units sold", "sales qty", "sales quantity", "sold", "数量", "销量", "销售数量", "出库", "出库数量", "件数"),
    "revenue": ("revenue", "sales", "amount", "gmv", "turnover", "net sales", "sales value", "sales amount", "value", "销售额", "金额", "销售金额", "营业额", "销售"),
    "price": ("price", "unit price", "asp", "selling price", "单价", "均价", "售价"),
    "stock": ("stock", "inventory", "on hand", "stock on hand", "closing stock", "库存", "剩余库存", "期末库存", "库存数量"),
}
METRIC_ROLES: dict[str, tuple[str, str, str | None]] = {  # role → (metric_key, fact_type, unit)
    "units": ("units_sold", "sales", "units"),
    "revenue": ("revenue", "sales", None),  # unit = the currency
    "price": ("unit_price", "sales", None),
    "stock": ("stock_on_hand", "inventory", "units"),
}
TOTAL_LABELS = ("合计", "总计", "小计", "总和", "total", "subtotal", "sum", "grand total", "summe", "totale")
CURRENCY_CODES = ("USD", "EUR", "CNY", "RMB", "GBP", "SEK", "NOK", "DKK", "JPY", "KRW", "HKD", "AUD", "CAD")
CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "CNY", "￥": "CNY", "₩": "KRW", "kr": "SEK"}
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})

_VOCAB_SORTED = sorted(((term, role) for role, terms in ROLE_VOCAB.items() for term in terms), key=lambda x: -len(x[0]))


def _norm_header(h: Any) -> str:
    s = str(h if h is not None else "").strip().lower()
    s = re.sub(r"\(.*?\)|（.*?）|\[.*?\]", " ", s)  # "(USD)" is a unit hint, not part of the name
    return re.sub(r"[\s_\-/:：]+", " ", s).strip()


def header_role(header: Any) -> str | None:
    key = _norm_header(header)
    if not key:
        return None
    for term, role in _VOCAB_SORTED:
        if key == term:
            return role
    for term, role in _VOCAB_SORTED:
        if len(term) >= 2 and (term in key.split() if term.isascii() else term in key):
            return role
    return None


def header_currency(header: Any) -> str | None:
    s = str(header or "")
    for code in CURRENCY_CODES:
        if re.search(rf"\b{code}\b", s, re.IGNORECASE):
            return "CNY" if code == "RMB" else code
    for sym, code in CURRENCY_SYMBOLS.items():
        if sym in s:
            return code
    return None


def is_total_label(value: Any) -> bool:
    s = str(value if value is not None else "").strip().lower()
    return bool(s) and any(s == t or s.startswith(t) or s.endswith(t) for t in TOTAL_LABELS)


# --- numbers and periods (code, never the model) -------------------------------------

def coerce_numeric(raw: Any) -> float | None:
    """A cell → number, or None. Accounting negatives, thousands vs decimal separators
    (the LAST separator is the decimal point), 万/亿, percentages → ratio, currency prefixes.
    Date labels ("9月", "第1周") are not numbers."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        return float(raw) if math.isfinite(float(raw)) else None
    if isinstance(raw, datetime | date):
        return None
    s = str(raw).strip()
    if not s or s.lower() in ("-", "—", "–", "n/a", "na", "null", "none", "nan"):
        return None
    if re.search(r"[年月日周季]$", s) or re.search(r"^\d{4}[-/.]\d{1,2}([-/.]\d{1,2})?$", s):
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1].strip()
    s = s.replace("−", "-")
    if s.startswith("-"):
        neg, s = not neg, s[1:].strip()
    pct = s.endswith("%") or s.endswith("％")
    if pct:
        s = s[:-1].strip()
    mult = 1.0
    if s.endswith("万"):
        mult, s = 1e4, s[:-1]
    elif s.endswith("亿"):
        mult, s = 1e8, s[:-1]
    # A currency marker is fine ("USD 99", "$1,200", "1200元"); any other letters mean it is not a number ("about 12").
    s = re.sub(r"(?i)(?<![a-z])(usd|eur|cny|rmb|gbp|sek|nok|dkk|jpy|krw|hkd|aud|cad|kr)(?![a-z])", "", s)
    s = re.sub(r"[$€£¥￥₩]|元|人民币|美元|\s", "", s)
    if not s or re.search(r"[^\d.,]", s) or not any(ch.isdigit() for ch in s):
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        s = s.replace(",", "") if all(len(p) == 3 for p in parts[1:]) else s.replace(",", ".")
    elif s.count(".") > 1:
        parts = s.split(".")
        if not all(len(p) == 3 for p in parts[1:]):
            return None
        s = s.replace(".", "")
    try:
        n = float(s)
    except ValueError:
        return None
    n = round(n * mult / (100 if pct else 1), 10)
    return -n if neg else n


def _month(y: int, m: int) -> tuple[str, str]:
    return date(y, m, 1).isoformat(), date(y, m, calendar.monthrange(y, m)[1]).isoformat()


def _quarter(y: int, q: int) -> tuple[str, str]:
    return _month(y, 3 * q - 2)[0], _month(y, 3 * q)[1]


def parse_period(value: Any, *, default_year: int | None = None) -> tuple[str, str] | None:
    """A period label → (start, end) as ISO dates. Months, quarters, years, days, in the
    forms reports actually use ("2025-08", "8月", "2025年8月", "Aug 2025", "Q3 2025",
    "2025-08-31"); a bare month borrows `default_year`. Weeks are left alone (ambiguous)."""
    if isinstance(value, datetime | date):
        d = value.date() if isinstance(value, datetime) else value
        return d.isoformat(), d.isoformat()
    s = str(value if value is not None else "").strip()
    if not s:
        return None
    if m := re.fullmatch(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", s):
        y, mo, d = (int(x) for x in m.groups())
        try:
            return date(y, mo, d).isoformat(), date(y, mo, d).isoformat()
        except ValueError:
            return None
    if m := re.fullmatch(r"(\d{4})[-/.年]\s*(\d{1,2})月?", s):
        y, mo = int(m.group(1)), int(m.group(2))
        return _month(y, mo) if 1 <= mo <= 12 else None
    if m := re.fullmatch(r"(\d{4})\s*[-/ ]?\s*q([1-4])", s, re.IGNORECASE) or re.fullmatch(r"q([1-4])\s*[-/ ]?\s*(\d{4})", s, re.IGNORECASE):
        a, b = m.groups()
        y, q = (int(a), int(b)) if len(a) == 4 else (int(b), int(a))
        return _quarter(y, q)
    if m := re.fullmatch(r"(\d{4})年?", s):
        y = int(m.group(1))
        return date(y, 1, 1).isoformat(), date(y, 12, 31).isoformat()
    if m := re.fullmatch(r"(\d{1,2})月", s):
        mo = int(m.group(1))
        return _month(default_year, mo) if default_year and 1 <= mo <= 12 else None
    words = re.split(r"[\s,.\-/]+", s.lower())
    month = next((MONTHS[w] for w in words if w in MONTHS), None)
    year = next((int(w) for w in words if re.fullmatch(r"\d{4}", w)), None)
    if month and (year or default_year):
        return _month(year or default_year, month)  # type: ignore[arg-type]
    return None


def period_bounds(reporting_period: str | None) -> tuple[str | None, str | None]:
    p = parse_period(reporting_period)
    return p if p else (None, None)


# --- reading the file ---------------------------------------------------------------------

@dataclass
class Table:
    sheet: str
    header_row: int  # 0-based row in the sheet
    headers: list[str]
    rows: list[list[Any]]  # data rows, raw cells

    @property
    def first_data_row(self) -> int:
        return self.header_row + 1


@dataclass
class Mapping:
    sheet: str
    header_row: int
    roles: dict[int, str]  # column index → role
    headers: list[str]
    currency: str | None
    period_columns: list[int] = field(default_factory=list)  # headers that are themselves periods (months as columns)

    def describe(self) -> list[dict[str, Any]]:
        return [
            {"column": h, "role": self.roles.get(i) or ("period column" if i in self.period_columns else "—")}
            for i, h in enumerate(self.headers)
        ]


def _grid(frame: Any) -> list[list[Any]]:
    return [[None if _blank(v) else v for v in row] for row in frame.itertuples(index=False, name=None)]


def _blank(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return isinstance(v, str) and not v.strip()


def read_grids(file_name: str, data: bytes) -> dict[str, list[list[Any]]]:
    """Every sheet as a grid of raw cells (CSV → one sheet). Headers are not interpreted here."""
    import pandas as pd

    name = file_name.lower()
    if name.endswith(".csv"):
        frame = pd.read_csv(io.BytesIO(data), header=None, dtype=object, keep_default_na=False)
        return {"Sheet1": _grid(frame)}
    if name.endswith((".xlsx", ".xlsm", ".xls")):
        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None, dtype=object)
        return {str(k): _grid(v) for k, v in sheets.items()}
    raise ValueError("Unsupported report file: use .xlsx or .csv")


def find_table(sheet: str, grid: list[list[Any]]) -> Table | None:
    """The first row that reads as a header (≥2 labels, ≥1 recognised role) and the rows
    under it until the sheet ends."""
    for r, row in enumerate(grid):
        labels = [c for c in row if isinstance(c, str) and c.strip()]
        if len(labels) < 2 or not any(header_role(c) for c in labels):
            continue
        rows = [x for x in grid[r + 1 :] if any(not _blank(c) for c in x)]
        if not rows:
            continue
        return Table(sheet=sheet, header_row=r, headers=[str(c).strip() if c is not None else "" for c in row], rows=rows)
    return None


def map_columns(table: Table, *, default_year: int | None = None) -> Mapping:
    roles: dict[int, str] = {}
    period_columns: list[int] = []
    currency = None
    for i, h in enumerate(table.headers):
        if not h:
            continue
        role = header_role(h)
        if role in METRIC_ROLES or role in ("sku", "product", "channel", "period"):
            # one label column per role; a second "product"-like header stays unmapped
            if role in ("sku", "product", "channel", "period") and role in roles.values():
                continue
            roles[i] = role
            currency = currency or (header_currency(h) if role in ("revenue", "price") else None)
        elif parse_period(h, default_year=default_year):
            period_columns.append(i)
    return Mapping(sheet=table.sheet, header_row=table.header_row, roles=roles, headers=table.headers, currency=currency, period_columns=period_columns)


# --- cells → facts -----------------------------------------------------------------------

class CatalogIndex:
    """SKU / name → product, so a fact carries the product_id an order line carries."""

    def __init__(self, products: list[Product]):
        self.by_sku = {p.sku.strip().lower(): p for p in products if p.sku}
        self.by_name = {n.strip().lower(): p for p in products for n in (p.product_name_en, p.native_name) if n}

    def match(self, sku: Any, name: Any) -> Product | None:
        if sku is not None and str(sku).strip().lower() in self.by_sku:
            return self.by_sku[str(sku).strip().lower()]
        if name is not None and str(name).strip().lower() in self.by_name:
            return self.by_name[str(name).strip().lower()]
        return None


def _text(v: Any) -> str | None:
    if _blank(v):
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def facts_from_table(
    table: Table, mapping: Mapping, *, default_period: tuple[str | None, str | None], currency: str | None,
    catalog: CatalogIndex | None = None, matrix_metric: str | None = None,
) -> tuple[list[ReportFact], list[str]]:
    """One fact per numeric metric cell. `matrix_metric` names what period columns measure
    (units | revenue | stock) when months run across the top; None leaves them unread."""
    facts: list[ReportFact] = []
    skipped: list[str] = []
    col_of = {role: i for i, role in mapping.roles.items()}
    cur = mapping.currency or currency
    label_cols = [c for r, c in col_of.items() if r in ("sku", "product", "channel", "period")]
    default_year = int(default_period[0][:4]) if default_period[0] else None

    def add(row_no: int, col: int, role: str, value: Any, *, period: tuple[str | None, str | None], sku: str | None, product: Product | None, entity: str | None, channel: str | None) -> None:
        n = coerce_numeric(value)
        header = mapping.headers[col]
        if n is None:
            if not _blank(value):
                skipped.append(f"{table.sheet} r{row_no + 1} '{header}': not a number: {str(value)[:40]}")
            return
        metric_key, fact_type, unit = METRIC_ROLES[role]
        facts.append(ReportFact(
            metric_key=metric_key, fact_type=fact_type, channel=channel, sku=sku, product_id=product.id if product else None,
            entity=entity, period_start=period[0], period_end=period[1], numeric_value=n,
            unit=unit or cur, currency=cur if unit is None else None,
            source_sheet=table.sheet, source_row=row_no + 1, source_col=col, source_header=header,
        ))

    for k, row in enumerate(table.rows):
        row_no = table.first_data_row + k
        cells = {i: (row[i] if i < len(row) else None) for i in range(len(mapping.headers))}
        if any(is_total_label(cells[c]) for c in label_cols):
            continue
        name = _text(cells[col_of["product"]]) if "product" in col_of else None
        sku_raw = _text(cells[col_of["sku"]]) if "sku" in col_of else None
        product = catalog.match(sku_raw, name) if catalog else None
        sku = sku_raw or (product.sku if product else None)
        channel = _text(cells[col_of["channel"]]) if "channel" in col_of else None
        period = default_period
        if "period" in col_of and not _blank(cells[col_of["period"]]):
            parsed = parse_period(cells[col_of["period"]], default_year=default_year)
            if parsed is None:
                skipped.append(f"{table.sheet} r{row_no + 1}: period not understood: {str(cells[col_of['period']])[:40]}")
                continue
            period = parsed
        entity = name or channel
        for col, role in mapping.roles.items():
            if role in METRIC_ROLES:
                add(row_no, col, role, cells[col], period=period, sku=sku, product=product, entity=entity, channel=channel)
        if matrix_metric in METRIC_ROLES:
            for col in mapping.period_columns:
                p = parse_period(mapping.headers[col], default_year=default_year)
                if p:
                    add(row_no, col, matrix_metric, cells[col], period=p, sku=sku, product=product, entity=entity, channel=channel)
    if mapping.period_columns and matrix_metric not in METRIC_ROLES:
        skipped.append("period columns left unread (say what they measure): " + ", ".join(mapping.headers[c] for c in mapping.period_columns))
    return facts, skipped


# --- the whole read, and the write ------------------------------------------------------

@dataclass
class Analysis:
    file_name: str
    sha256: str
    size_bytes: int
    reporting_period: str | None
    period_start: str | None
    period_end: str | None
    mappings: list[Mapping]
    facts: list[ReportFact]
    skipped: list[str]

    @property
    def raw(self) -> dict[str, Any]:
        return {
            "reporting_period": self.reporting_period,
            "tables": [
                {"sheet": m.sheet, "header_row": m.header_row, "columns": m.describe(), "currency": m.currency} for m in self.mappings
            ],
        }


def analyze(
    file_name: str, data: bytes, *, reporting_period: str | None, currency: str | None, products: list[Product] | None = None,
    matrix_metric: str | None = None,
) -> Analysis:
    """Read every sheet, map its columns, turn the cells into draft facts. Pure — nothing is written."""
    start, end = period_bounds(reporting_period)
    default_year = int(start[:4]) if start else None
    catalog = CatalogIndex(products) if products else None
    mappings: list[Mapping] = []
    facts: list[ReportFact] = []
    skipped: list[str] = []
    for sheet, grid in read_grids(file_name, data).items():
        table = find_table(sheet, grid)
        if table is None:
            skipped.append(f"{sheet}: no table found")
            continue
        mapping = map_columns(table, default_year=default_year)
        mappings.append(mapping)
        if not any(r in METRIC_ROLES for r in mapping.roles.values()) and not mapping.period_columns:
            skipped.append(f"{sheet}: no numeric column recognised (headers: {', '.join(h for h in table.headers if h)[:120]})")
            continue
        f, s = facts_from_table(table, mapping, default_period=(start, end), currency=currency, catalog=catalog, matrix_metric=matrix_metric)
        facts.extend(f)
        skipped.extend(s)
    return Analysis(
        file_name=file_name, sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), reporting_period=reporting_period,
        period_start=start, period_end=end, mappings=mappings, facts=facts, skipped=skipped,
    )


def ingest(db: Database, partnership: Partnership, analysis: Analysis, *, title: str | None = None, notes: str | None = None) -> SalesReport:
    """Write the report, the extraction run and its draft facts in one transaction."""
    report = SalesReport(
        id=f"rep_{uuid.uuid4().hex[:12]}", brand_id=partnership.brand_id, partnership_id=partnership.id,
        title=title or analysis.file_name, file_name=analysis.file_name, file_sha256=analysis.sha256,
        media_type="text/csv" if analysis.file_name.lower().endswith(".csv") else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        size_bytes=analysis.size_bytes, reporting_period=analysis.reporting_period,
        period_start=analysis.period_start, period_end=analysis.period_end, notes=notes,
    )
    db.save_report(report, method="tidy_table", model=None, raw=analysis.raw, skipped=analysis.skipped, facts=analysis.facts)
    return report
