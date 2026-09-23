"""Reports: numbers are read from cells by code, totals are dropped, facts are draft until
confirmed, and a re-sent month supersedes instead of doubling."""

from __future__ import annotations

import io

import pytest

from partnerdesk import reports
from partnerdesk.models import Partnership, Product
from partnerdesk.reports import analyze, coerce_numeric, header_currency, header_role, ingest, parse_period

PRODUCTS = [
    Product(id="p-fo", sku="FO-100", product_name_en="Deep Sea Fish Oil", native_name="深海鱼油omega-3"),
    Product(id="p-sr", sku="SR-200", product_name_en="Radiance Serum"),
]

CSV = """SKU,Product,Channel,Month,Units sold,Sales (USD)
FO-100,Deep Sea Fish Oil,Tmall,2025-08,"1,200","12,000.50"
,Radiance Serum,JD,2025-08,300,4500
SR-200,Radiance Serum,Tmall,2025-09,150,"2,250"
Total,,,,"1,650","18,750.50"
"""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,234", 1234.0), ("1.234,56", 1234.56), ("1,234.56", 1234.56), ("1,5", 1.5), ("1.234.567", 1234567.0),
        ("(21,701.50)", -21701.5), ("-42", -42.0), ("28.4万", 284000.0), ("1.2亿", 120_000_000.0),
        ("35.6%", 0.356), ("$1,200", 1200.0), ("USD 99", 99.0), (897, 897.0), (12.5, 12.5),
        ("9月", None), ("第1周", None), ("2025-08", None), ("2025/08/31", None), ("", None), (None, None), ("n/a", None), (True, None),
    ],
)
def test_coerce_numeric(raw, expected):
    assert coerce_numeric(raw) == expected


@pytest.mark.parametrize(
    ("label", "year", "expected"),
    [
        ("2025-08", None, ("2025-08-01", "2025-08-31")), ("2025/8", None, ("2025-08-01", "2025-08-31")),
        ("2025年8月", None, ("2025-08-01", "2025-08-31")), ("8月", 2025, ("2025-08-01", "2025-08-31")), ("8月", None, None),
        ("August 2025", None, ("2025-08-01", "2025-08-31")), ("Aug 2025", None, ("2025-08-01", "2025-08-31")), ("April", 2026, ("2026-04-01", "2026-04-30")),
        ("Q3 2025", None, ("2025-07-01", "2025-09-30")), ("2025-Q3", None, ("2025-07-01", "2025-09-30")), ("2025Q1", None, ("2025-01-01", "2025-03-31")),
        ("2025-08-31", None, ("2025-08-31", "2025-08-31")), ("2026年2月29日", None, None), ("2025", None, ("2025-01-01", "2025-12-31")),
        ("第一周", 2025, None), ("total", None, None),
    ],
)
def test_parse_period(label, year, expected):
    assert parse_period(label, default_year=year) == expected


def test_header_roles_in_two_languages():
    assert header_role("SKU") == "sku" and header_role("货号") == "sku"
    assert header_role("Units sold") == "units" and header_role("销售数量") == "units" and header_role("Sales qty") == "units"
    assert header_role("Sales (USD)") == "revenue" and header_role("销售额") == "revenue"
    assert header_role("渠道") == "channel" and header_role("月份") == "period" and header_role("库存") == "stock"
    assert header_role("Remarks") is None
    assert header_currency("Sales (USD)") == "USD" and header_currency("金额（RMB）") == "CNY" and header_currency("Units") is None


def test_csv_becomes_facts_without_the_total_row():
    a = analyze("aug.csv", CSV.encode("utf-8"), reporting_period="2025-08", currency="USD", products=PRODUCTS)
    assert a.period_start == "2025-08-01" and a.period_end == "2025-08-31"
    (m,) = a.mappings
    assert {m.headers[i]: r for i, r in m.roles.items()} == {"SKU": "sku", "Product": "product", "Channel": "channel", "Month": "period", "Units sold": "units", "Sales (USD)": "revenue"}
    assert m.currency == "USD"
    assert len(a.facts) == 6  # 3 rows × (units, revenue); the Total row is dropped
    fish = [f for f in a.facts if f.sku == "FO-100"]
    assert {f.metric_key: f.numeric_value for f in fish} == {"units_sold": 1200.0, "revenue": 12000.5}
    assert all(f.product_id == "p-fo" and f.channel == "Tmall" and f.period_start == "2025-08-01" for f in fish)
    # No SKU column value → the product name resolves it against the catalog.
    serum_jd = next(f for f in a.facts if f.channel == "JD" and f.metric_key == "units_sold")
    assert serum_jd.sku == "SR-200" and serum_jd.product_id == "p-sr" and serum_jd.numeric_value == 300
    # A row with its own month overrides the report period.
    sept = next(f for f in a.facts if f.period_start == "2025-09-01")
    assert sept.sku == "SR-200" and sept.period_end == "2025-09-30"
    rev = next(f for f in a.facts if f.metric_key == "revenue")
    assert rev.currency == "USD" and rev.unit == "USD" and rev.source_header == "Sales (USD)" and rev.source_row == 2
    assert all(f.status == "draft" and not f.ai_derived for f in a.facts)
    assert a.skipped == []


def _xlsx(rows: list[list]) -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "销售"
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx_months_across_the_top_need_to_be_told_what_they_measure():
    data = _xlsx([["2025年销售情况", None, None], ["产品", "8月", "9月"], ["深海鱼油omega-3", "1,200", "1,350"], ["合计", "1,200", "1,350"]])
    unread = analyze("sales.xlsx", data, reporting_period="2025", currency="CNY", products=PRODUCTS)
    assert unread.facts == [] and any("period columns left unread" in s for s in unread.skipped)
    (m,) = unread.mappings
    assert m.header_row == 1 and [m.headers[c] for c in m.period_columns] == ["8月", "9月"]

    read = analyze("sales.xlsx", data, reporting_period="2025", currency="CNY", products=PRODUCTS, matrix_metric="units")
    assert [(f.period_start, f.numeric_value, f.product_id) for f in read.facts] == [("2025-08-01", 1200.0, "p-fo"), ("2025-09-01", 1350.0, "p-fo")]
    assert read.skipped == []


def test_unreadable_cells_are_reported_not_invented():
    csv = "SKU,Units\nFO-100,about 12\nSR-200,40\n"
    a = analyze("x.csv", csv.encode(), reporting_period="2025-08", currency="USD", products=PRODUCTS)
    assert [f.numeric_value for f in a.facts] == [40.0]
    assert a.skipped == ["Sheet1 r2 'Units': not a number: about 12"]


def test_confirm_promotes_drafts_and_a_resend_supersedes(db, partnership: Partnership):
    a = analyze("aug.csv", CSV.encode("utf-8"), reporting_period="2025-08", currency="USD", products=PRODUCTS)
    first = ingest(db, partnership, a, title="August")
    assert db.report(first.id).status == "draft" and db.confirmed_facts(partnership.id) == []
    assert len(db.report_facts(first.id, status="draft")) == 6
    (run,) = db.report_extractions(first.id)
    assert run["method"] == "tidy_table" and run["raw"]["tables"][0]["columns"][0] == {"column": "SKU", "role": "sku"}

    assert db.confirm_report(first.id) == {"confirmed": 6, "superseded": 0}
    assert db.report(first.id).status == "confirmed" and len(db.confirmed_facts(partnership.id)) == 6
    assert db.confirm_report(first.id) == {"confirmed": 0, "superseded": 0}  # idempotent

    # The same month sent again with a corrected number: the new facts win, the count stays.
    corrected = CSV.replace('"1,200"', '"1,250"')
    second = ingest(db, partnership, analyze("aug-v2.csv", corrected.encode(), reporting_period="2025-08", currency="USD", products=PRODUCTS), title="August (corrected)")
    assert db.confirm_report(second.id) == {"confirmed": 6, "superseded": 6}
    live = db.confirmed_facts(partnership.id)
    assert len(live) == 6 and all(f.report_id == second.id for f in live)
    assert next(f.numeric_value for f in live if f.sku == "FO-100" and f.metric_key == "units_sold") == 1250.0
    assert {f.status for f in db.report_facts(first.id)} == {"superseded"}

    db.delete_report(second.id)
    assert db.report(second.id) is None and db.report_facts(second.id) == []


def test_reports_list_is_newest_period_first(db, partnership: Partnership):
    for period, name in (("2025-07", "jul"), ("2025-09", "sep"), ("2025-08", "aug")):
        ingest(db, partnership, analyze(f"{name}.csv", b"SKU,Units\nFO-100,1\n", reporting_period=period, currency="USD"), title=name)
    assert [r.title for r in db.reports(partnership.id)] == ["sep", "aug", "jul"]
    assert reports.period_bounds("2025-Q4") == ("2025-10-01", "2025-12-31") and reports.period_bounds(None) == (None, None)
