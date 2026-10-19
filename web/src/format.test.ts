import { describe, expect, it } from "vitest";

import {
  buildLedger,
  chatTitle,
  citationSources,
  confirmReady,
  dataQueryMark,
  discountText,
  greeting,
  lastMonth,
  lineFlag,
  liveSummary,
  money,
  orderedVsSold,
  revenueLabel,
  sumBy,
  unitsLabel,
} from "./format";

describe("desk formatters", () => {
  it("formats money the way the order panel did", () => {
    expect(money("USD", 1000)).toBe("USD 1,000.00");
    expect(money("USD", null)).toBe("—");
  });

  it("keeps Confirm disabled until both early steps are done", () => {
    expect(confirmReady([
      { title: "Understand request", status: "pending" },
      { title: "Check inputs", status: "pending" },
      { title: "Confirm draft", status: "needs-you" },
    ])).toBe(false);
    expect(confirmReady(buildLedger("confirm"))).toBe(true);
    expect(confirmReady(buildLedger("gathering"))).toBe(false);
  });

  it("keeps a data-query rating only when the log id is a positive integer", () => {
    expect(dataQueryMark({ log_id: 4, feedback: "bad" })).toEqual({ logId: 4, feedback: "bad" });
    expect(dataQueryMark({ log_id: 4, feedback: "maybe" })).toEqual({ logId: 4, feedback: undefined });
    expect(dataQueryMark({ log_id: 0 })).toBeUndefined();
    expect(dataQueryMark(null)).toBeUndefined();
  });

  it("titles a chat from the session title, else the first user line", () => {
    expect(chatTitle({ title: "10 cases fish oil" })).toBe("10 cases fish oil");
    expect(chatTitle({ title: null, history: [{ role: "user", content: "hello there" }] })).toBe("hello there");
    expect(chatTitle({ title: null, history: [{ role: "user", content: "(confirm)" }] })).toBe("New chat");
  });

  it("keeps numbered file citations and drops a blank one", () => {
    expect(citationSources([
      { n: 1, source: "品牌手册.pdf，第3页" },
      { n: 2, source: "  " },
      { n: "3", source: "价目表.pdf，第1页" },
    ])).toEqual([
      { n: 1, source: "品牌手册.pdf，第3页" },
      { n: 3, source: "价目表.pdf，第1页" },
    ]);
    expect(citationSources(undefined)).toBeUndefined();
  });

  it("greets with the partnership names", () => {
    expect(greeting({ distributor_name: "Nordic", brand_name: "Aurora" })).toContain("Nordic");
    expect(greeting({ distributor_name: "Nordic", brand_name: "Aurora" })).toContain("What would you like");
  });

  it("flags a draft line and describes a contract discount", () => {
    expect(lineFlag({
      name: "Serum", sku: null, reference: "the serum", cases: 10, case_price: null, line_total: null,
      moq_cases: null, ambiguous: true, unresolved: false, below_moq: false, missing_price: false,
      partial_layer: false, cases_to_full_layer: null,
    })).toBe("which one?");
    expect(discountText(null, "USD")).toBe("not yet decided");
    expect(discountText({ kind: "percent", value: 3, source: "contract" }, "USD")).toBe("3% · per contract");
    expect(discountText({ kind: "none" }, "USD")).toBe("none");
  });

  it("rolls the default reporting period back one month", () => {
    expect(lastMonth(new Date(2026, 9, 15))).toBe("2026-09");
    expect(lastMonth(new Date(2026, 0, 2))).toBe("2025-12");
  });

  it("sums confirmed sell-out the way the reports page did", () => {
    const facts = [
      fact("units_sold", "FO-100", 120, "2025-08-01"),
      fact("revenue", "FO-100", 1200, "2025-08-01"),
      fact("units_sold", "SR-200", 30, "2025-08-01"),
      fact("revenue", "SR-200", 1500, "2025-08-01"),
    ];
    const summary = liveSummary(facts, "USD");
    expect(revenueLabel(summary)).toBe("USD 2,700");
    expect(unitsLabel(summary.units)).toBe("150");
    expect(summary.months).toBe(1);
    expect(summary.skus).toBe(2);
    expect(sumBy(summary.revenueRows, (f) => (f.period_start || "").slice(0, 7))).toEqual([
      { label: "2025-08", value: 2700 },
    ]);
  });

  it("compares ordered units with reported sell-out", () => {
    const rows = orderedVsSold(
      [{ items: [{ sku: "FO-100", quantity: 10, case_pack: 12, product_name: "Fish Oil" }] }],
      [fact("units_sold", "FO-100", 60, "2025-08-01")],
    );
    expect(rows).toEqual([{ sku: "FO-100", product: "Fish Oil", ordered: 120, sold: 60, sellThrough: 0.5 }]);
  });
});

function fact(metric: string, sku: string, value: number, period: string) {
  return {
    fact_type: "sales",
    metric_key: metric,
    numeric_value: value,
    currency: "USD",
    period_start: period,
    sku,
    entity: sku,
    channel: null,
  };
}
