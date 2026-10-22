import { describe, expect, it } from "vitest";

import { parseMarkdown, shapeProse } from "./markdown";

const REPORT = `## 本期情况
由于数据查询未返回有效结果，本期的订单量和售罄率数据缺失。

## 缺口
本报告目前缺失以下标准数据：
1. 本期的订单量（Orders）
2. 本期的售罄率（Sell-through）
3. 上一期的订单量与售罄率
`;

describe("parseMarkdown", () => {
  it("turns a report draft into headings, paragraphs, and a numbered list", () => {
    expect(parseMarkdown(REPORT)).toEqual([
      { kind: "heading", level: 2, inlines: [{ kind: "text", text: "本期情况" }] },
      {
        kind: "paragraph",
        lines: [[{ kind: "text", text: "由于数据查询未返回有效结果，本期的订单量和售罄率数据缺失。" }]],
      },
      { kind: "heading", level: 2, inlines: [{ kind: "text", text: "缺口" }] },
      { kind: "paragraph", lines: [[{ kind: "text", text: "本报告目前缺失以下标准数据：" }]] },
      {
        kind: "list",
        ordered: true,
        items: [
          [{ kind: "text", text: "本期的订单量（Orders）" }],
          [{ kind: "text", text: "本期的售罄率（Sell-through）" }],
          [{ kind: "text", text: "上一期的订单量与售罄率" }],
        ],
      },
    ]);
  });

  it("keeps a plain reply as one paragraph, including its line break", () => {
    expect(parseMarkdown("好的，已记下。\n请确认数量。")).toEqual([
      {
        kind: "paragraph",
        lines: [
          [{ kind: "text", text: "好的，已记下。" }],
          [{ kind: "text", text: "请确认数量。" }],
        ],
      },
    ]);
  });

  it("draws a pipe table and keeps a chart link", () => {
    expect(parseMarkdown([
      "| 指标 | 9月 |",
      "|:---|---:|",
      "| 销售额 | 3,491.93 |",
      "",
      "详见 [销售趋势](chart_001)。",
    ].join("\n"))).toEqual([
      {
        kind: "table",
        aligns: ["left", "right"],
        header: [
          [{ kind: "text", text: "指标" }],
          [{ kind: "text", text: "9月" }],
        ],
        rows: [[
          [{ kind: "text", text: "销售额" }],
          [{ kind: "text", text: "3,491.93" }],
        ]],
      },
      {
        kind: "paragraph",
        lines: [[
          { kind: "text", text: "详见 " },
          { kind: "link", text: "销售趋势", href: "chart_001" },
          { kind: "text", text: "。" },
        ]],
      },
    ]);
  });

  it("splits a long report paragraph and leaves the table intact", () => {
    const shaped = shapeProse("销售额上升。库存偏紧。需要补货。还要看节假日。\n| 指标 | 本月 |\n|---|---|\n| 销售额 | 1 |");
    expect(shaped).toBe([
      "销售额上升。库存偏紧。",
      "需要补货。还要看节假日。",
      "| 指标 | 本月 |\n|---|---|\n| 销售额 | 1 |",
    ].join("\n\n"));
  });

  it("marks bold and code without treating a bold line as a bullet", () => {
    expect(parseMarkdown("- 渠道 **缺失**\n- 见 `SKU`")).toEqual([
      {
        kind: "list",
        ordered: false,
        items: [
          [
            { kind: "text", text: "渠道 " },
            { kind: "strong", text: "缺失" },
          ],
          [
            { kind: "text", text: "见 " },
            { kind: "code", text: "SKU" },
          ],
        ],
      },
    ]);
    expect(parseMarkdown("**不是列表**")).toEqual([
      { kind: "paragraph", lines: [[{ kind: "strong", text: "不是列表" }]] },
    ]);
  });
});
