"""Chat report draft: questions and web lookups are slots; charts are the model's ECharts options."""

from __future__ import annotations

from backend.report.analyze import column_kinds, cover_days, findings
from backend.report.prompt import (
    CHART_GENERATION_PROMPT,
    CODE_WIZARD_PROMPT,
    REPORT_QUESTIONS_PROMPT,
    REPORT_REVIEW_PROMPT,
    REPORT_REVISE_PROMPT,
    SECTIONS,
    SUMMARY_PROMPT,
    WRITING_PROMPT,
    outline_text,
)
from backend.report.turn import (
    finish_report,
    parse_plan,
    place_charts_in_sections,
    relabel_chart_labels,
    rewrite_product_mentions,
    shape_prose,
    stamp_product_labels,
)
from backend.report.wizard import execute


def test_cover_days_divides_stock_by_period_units():
    notes = cover_days([
        [{"sku": "FO-100", "stock_on_hand": 90}],
        [{"sku": "FO-100", "units_sold": 30}, {"sku": "FO-200", "units_sold": 10}],
    ])
    assert notes == [{"sku": "FO-100", "stock": 90.0, "units": 30.0, "cover_days": 90.0}]


def test_parse_plan_keeps_six_sections_and_drops_a_search_on_a_data_section():
    from datetime import date

    planned = parse_plan({
        "sections": [
            {"id": "performance", "questions": [" A ", "a", "b", "c", "d"], "searches": ["should not search"]},
            {"id": "nope", "questions": ["x"]},
            {"id": "next", "questions": ["sql?"], "searches": [" 双十一 ", "black friday", "618"]},
            {"id": "risks", "questions": ["amount"], "searches": []},
            {"id": "summary", "questions": ["skip"], "searches": ["skip"]},
        ],
    }, today=date(2026, 10, 19))
    assert [section.id for section in planned] == [section["id"] for section in SECTIONS]
    performance = next(section for section in planned if section.id == "performance")
    assert performance.questions == ["A", "b", "c"]
    assert performance.searches == []
    nxt = next(section for section in planned if section.id == "next")
    assert nxt.questions == []
    assert nxt.searches == ["双十一", "black friday"]
    risks = next(section for section in planned if section.id == "risks")
    assert risks.questions == ["amount"]
    assert risks.searches == []
    summary = next(section for section in planned if section.id == "summary")
    assert summary.questions == [] and summary.searches == []


def test_parse_plan_does_not_invent_questions_when_the_model_returns_nothing():
    from datetime import date

    planned = parse_plan({}, today=date(2026, 10, 19))
    performance = next(section for section in planned if section.id == "performance")
    assert performance.questions == []
    metrics = next(section for section in planned if section.id == "metrics")
    assert metrics.questions == []
    nxt = next(section for section in planned if section.id == "next")
    assert nxt.searches == ["2026年10月 零售档期 促销热点", "即将到来的节假日 零售促销"]
    summary = next(section for section in planned if section.id == "summary")
    assert summary.questions == [] and summary.searches == []
    risks = next(section for section in planned if section.id == "risks")
    assert risks.questions == [] and risks.searches == []


def test_prompts_use_the_sectioned_shape_and_format_cleanly():
    assert [section["id"] for section in SECTIONS] == [
        "metrics", "summary", "performance", "inventory", "next", "risks",
    ]
    outline = outline_text()
    assert "关键指标" in outline and "问题与风险" in outline and "不要原样照抄" in outline
    for prompt in (
        REPORT_QUESTIONS_PROMPT, CHART_GENERATION_PROMPT, WRITING_PROMPT, SUMMARY_PROMPT,
        CODE_WIZARD_PROMPT, REPORT_REVIEW_PROMPT, REPORT_REVISE_PROMPT,
    ):
        assert "## 角色" in prompt and "## 输入定义" in prompt
    assert "问题与风险由你判断" in REPORT_QUESTIONS_PROMPT
    planned = REPORT_QUESTIONS_PROMPT.format(
        outline=outline_text(), message="写一份报告", database_structure="- contracts: 协议",
        max_questions=3, max_searches=2,
    )
    assert "写一份报告" in planned and "{outline}" not in planned
    chart = CHART_GENERATION_PROMPT.format(message="月报", query_results="[]")
    assert "{c}%" in chart and "860" in chart
    WRITING_PROMPT.format(message="月报", section_title="业绩表现", section_brief="销量")
    SUMMARY_PROMPT.format(message="月报")
    filled = CODE_WIZARD_PROMPT.format(
        section_title="库存情况",
        section_brief="采购与库存剩余",
        questions='["期末库存"]',
        query_results='[{"question": "期末库存", "rows": []}]',
    )
    assert "库存情况" in filled and "期末库存" in filled
    assert "{section_title}" not in filled and "{query_results}" not in filled
    reviewed = REPORT_REVIEW_PROMPT.format(
        message="月报", outline="关键指标", report="正文", evidence="{}", previous_issues="（无）",
    )
    assert "月报" in reviewed and "{report}" not in reviewed
    revised = REPORT_REVISE_PROMPT.format(
        message="月报", report="正文", score=70, chapters="[]", issues="[]", evidence="{}",
    )
    assert "正文" in revised and "{issues}" not in revised


def test_insight_code_reads_the_rows_and_rejects_an_import():
    ran = execute(
        "def analyze(datasets):\n"
        "    rows = datasets[0]['rows']\n"
        "    return [{'title': '订单', 'detail': '两行', 'evidence': {'n': len(rows), 'total': rows[0]['a'] + rows[1]['a']}}]",
        [{"id": "q1", "question": "合计", "rows": [{"a": 1}, {"a": 2}]}],
    )
    assert ran["error"] is None
    assert ran["insights"] == [{"title": "订单", "detail": "两行", "evidence": {"n": 2, "total": 3}}]
    blocked = execute("import os\ninsights = []", [])
    assert blocked["insights"] == [] and blocked["error"]


def test_five_chapters_add_up_and_a_missing_one_scores_zero():
    from backend.report.turn import _review_score

    raw = {"chapters": [
        {"section": "关键指标", "score": 20},
        {"section": "摘要", "score": 20},
        {"section": "业绩表现", "score": 20},
        {"section": "库存情况", "score": 20},
        {"section": "下一周期计划", "score": 5},
    ], "risks_penalty": 0, "score": 99}
    assert _review_score(raw)[0] == 85
    raw["risks_penalty"] = 10
    assert _review_score(raw)[0] == 75
    raw["chapters"] = [item for item in raw["chapters"] if item["section"] != "摘要"]
    raw["risks_penalty"] = 0
    assert _review_score(raw)[0] == 65
    assert _review_score({"score": 90})[0] == 90


def test_review_passes_at_85_and_revises_below_it_for_three_rounds():
    from backend.llm import FakeLLM
    from backend.report.turn import _review_rounds

    def run(replies: list[dict], report: str = "原文"):
        llm = FakeLLM(json_replies=replies)
        text, score, error = _review_rounds(llm, message="月报", report=report, evidence="{}", remembered="")
        return llm, text, score, error

    _, text, score, error = run([{"score": 85, "ok": True, "issues": []}])
    assert text == "原文" and score == 85 and error is None

    issue = {"section": "业绩表现", "problem": "没有提到图表"}
    llm, text, score, error = run([
        {"score": 84, "ok": False, "issues": [issue]},
        {"report": "改后的报告"},
        {"score": 90, "ok": True, "issues": []},
    ])
    assert text == "改后的报告" and score == 90 and error is None
    assert "没有提到图表" in llm.calls[1]["system"] and "84" in llm.calls[1]["system"]
    assert "没有提到图表" in llm.calls[2]["system"]

    replies: list[dict] = []
    for index in range(1, 4):
        replies.append({"score": 60, "ok": False, "issues": [issue]})
        replies.append({"report": f"第{index}稿"})
    replies.append({"score": 70, "ok": False, "issues": [issue]})
    llm, text, score, error = run(replies, report="原稿")
    assert text == "第3稿" and score == 70 and error is None
    assert len(llm.calls) == 7


def test_reflection_retries_a_failure_and_keeps_three_tries():
    from backend.llm import FakeLLM
    from backend.report.reflect import with_reflection

    def fail_then_ok(artifact: str):
        if artifact == "ok":
            return True, "done", None, ""
        return False, None, f"bad {artifact}", "stdout"

    fixed = FakeLLM(json_replies=[{"fixed_code": "ok"}])
    value, failures = with_reflection(fixed, kind="code", first="bad", run=fail_then_ok)
    assert value == "done" and failures == []
    assert "bad bad" in fixed.calls[0]["system"]

    def always_fail(_artifact: str):
        return False, None, "boom", "out"

    stuck = FakeLLM(json_replies=[{"fixed_code": "still"}, {"fixed_code": "nope"}])
    value, failures = with_reflection(stuck, kind="sql", first="a", run=always_fail)
    assert value is None and len(failures) == 3
    assert [item["artifact"] for item in failures] == ["a", "still", "nope"]
    assert all(item["error"] == "boom" for item in failures)


def test_findings_sum_a_mix_and_a_trend_and_skip_a_single_row():
    rows = [
        {"period": "2025-08", "channel": "Tmall", "units": 10},
        {"period": "2025-08", "channel": "JD", "units": 4},
        {"period": "2025-09", "channel": "Tmall", "units": 12},
        {"period": "2025-09", "channel": "JD", "units": 6},
    ]
    found = findings("q1", rows)
    trend = next(item for item in found if item["kind"] == "trend")
    mix = next(item for item in found if item["kind"] == "mix")
    assert trend["start"] == {"label": "2025-08", "value": 14}
    assert trend["end"] == {"label": "2025-09", "value": 18}
    assert trend["change"] == 4 and trend["change_percent"] == 28.6 and trend["direction"] == "up"
    assert mix["total"] == 32 and mix["group_count"] == 2
    assert mix["top"] == {"label": "Tmall", "value": 22, "share_percent": 68.8}
    assert mix["bottom"]["label"] == "JD"
    assert findings("q2", [{"total_amount": 1000}]) == []
    assert column_kinds([{"period": "2025-08", "units": 1}]) == [
        {"name": "period", "kind": "time"},
        {"name": "units", "kind": "number"},
    ]


def test_findings_range_when_there_is_no_category():
    assert findings("q1", [{"units": 2}, {"units": 4}]) == [{
        "id": "q1", "kind": "range", "y": "units", "count": 2, "min": 2, "max": 4, "mean": 3,
    }]


def test_product_names_replace_a_bare_sku_and_charts_stay_in_their_section():
    from backend.report.turn import SectionPlan

    rows = [{"sku": "FO-100", "units": 30}, {"sku": "SR-200", "product_name": "精华", "units": 10}]
    labels = stamp_product_labels(rows, {"FO-100": "深海鱼油"})
    assert rows[0]["product_label"] == "深海鱼油（FO-100）"
    assert rows[1]["product_label"] == "精华（SR-200）"
    rewritten = rewrite_product_mentions("FO-100 卖了 30。深海鱼油（FO-100）仍是主力。FO-1000 另算。", labels)
    assert rewritten.startswith("深海鱼油（FO-100） 卖了 30。")
    assert "深海鱼油（深海鱼油" not in rewritten
    assert "FO-1000" in rewritten
    assert shape_prose("销售额上升。库存偏紧。需要补货。还要看节假日。") == (
        "销售额上升。库存偏紧。\n\n需要补货。还要看节假日。"
    )

    chart = {
        "id": "chart_001", "title": "FO-100 销量", "subtitle": "",
        "echarts_option": {"xAxis": {"type": "category", "data": ["FO-100", "SR-200"]}},
    }
    relabeled = relabel_chart_labels([chart], labels)
    assert chart["title"] == "FO-100 销量"
    assert relabeled[0]["id"] == "chart_001"
    assert relabeled[0]["title"] == "深海鱼油（FO-100） 销量"
    assert relabeled[0]["echarts_option"]["xAxis"]["data"] == ["深海鱼油（FO-100）", "精华（SR-200）"]
    assert relabeled[0]["echarts_option"]["xAxis"]["axisLabel"]["rotate"] == 28

    planned = [
        SectionPlan("summary", "摘要", "", False, False, False, False, [], []),
        SectionPlan("performance", "业绩表现", "", True, True, False, False, [], []),
    ]
    report = "## 2. 摘要\n\n见 [趋势](chart_001)。\n\n## 3. 业绩表现\n\n销量上升。"
    placed = place_charts_in_sections(report, planned, {"performance": relabeled})
    summary, performance = placed.split("业绩表现", 1)
    assert "chart_001" not in summary
    assert "(chart_001)" in performance
    finished = finish_report(report, labels, planned, {"performance": relabeled})
    assert "chart_001" in finished.split("业绩表现", 1)[1]
