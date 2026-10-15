"""Heading splits are deterministic. No model, no OCR."""

from __future__ import annotations

from partnerdesk.contract.chunks import split_chunks

AGREEMENT = [
    (1, """本协议由双方于 2026-01-01 签订。

第一条 定义
1.1 "Container" means one shipping container.
1.1.1 Size means a twenty-foot container.
1.2 Territory means Sweden.
（一）仅供说明
第二条 订货
最低订单金额为 USD 500。
(a) 海运
(b) 空运
"""),
    (2, """2.1 折扣
第一柜和第二柜按标价减 3%。
"""),
]


def test_headings_nest_and_keep_the_preamble():
    tree = split_chunks(AGREEMENT)
    assert tree[0]["number"] == "" and "2026-01-01" in tree[0]["body"] and tree[0]["path"] == ""
    article = tree[1]
    assert article["number"] == "第一条" and article["title"] == "定义" and article["page_start"] == 1
    one_one, one_two = article["children"]
    assert one_one["number"] == "1.1" and one_one["title"] == ""
    assert "shipping container" in one_one["body"]
    assert one_one["children"][0]["number"] == "1.1.1" and one_one["children"][0]["path"] == "第一条 / 1.1 / 1.1.1"
    assert one_two["number"] == "1.2" and "（一）仅供说明" in one_two["body"]
    order = tree[2]
    assert order["number"] == "第二条" and order["title"] == "订货"
    assert "(a) 海运" in order["body"] and "(b) 空运" in order["body"]
    assert order["page_end"] == 1
    discount = order["children"][0]
    assert discount["number"] == "2.1" and discount["title"] == "折扣"
    assert discount["page_start"] == 2 and "3%" in discount["body"]
    assert [node["id"] for node in tree] == ["c1", "c2", "c6"]


def test_chinese_enumeration_is_top_level_only_outside_an_article():
    pages = [(1, "一、定义\n术语以本合同为准。\n二、期限\n自 2026-01-01 起。")]
    tree = split_chunks(pages)
    assert [node["number"] for node in tree] == ["一", "二"]
    assert tree[0]["title"] == "定义" and "术语" in tree[0]["body"]
    inside = [(1, "第一条 定义\n一、经销商应遵守本条。")]
    nested = split_chunks(inside)
    assert nested[0]["number"] == "第一条" and nested[0]["children"] == []
    assert "一、经销商应遵守本条。" in nested[0]["body"]


def test_markdown_heading_marks_are_ignored():
    pages = [(3, "# 第一条 区域\n瑞典与挪威。")]
    (node,) = split_chunks(pages)
    assert node["number"] == "第一条" and node["title"] == "区域"
    assert node["page_start"] == 3 and node["page_end"] == 3 and "挪威" in node["body"]


def test_english_article_keeps_decimal_children():
    pages = [(1, "Article 1 — Definitions\n1.1 Container means one shipment.\nSection 2 Term\nTwo years.")]
    tree = split_chunks(pages)
    assert tree[0]["number"] == "1" and tree[0]["title"] == "Definitions"
    assert tree[0]["children"][0]["number"] == "1.1" and tree[0]["children"][0]["path"] == "1 / 1.1"
    assert tree[1]["number"] == "2" and tree[1]["title"] == "Term"
