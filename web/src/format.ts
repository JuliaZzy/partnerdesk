/** Pure desk formatting. The page renders these; it does not recompute prices or rules. */

export type Step = { title: string; status: string };

export type Partnership = {
  id: string;
  brand_id: string;
  brand_name: string;
  distributor_name: string;
  ship_to_country: string | null;
  currency: string;
};

export type HistoryMessage = { role: string; content?: string };

export type Citation = { n: number; source: string };

export type DataQueryMark = { logId: number; feedback?: "good" | "bad" };

/** The numbered file-and-page lines under a brand answer. Bad rows are dropped. */
export function citationSources(raw: unknown): Citation[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const sources = raw.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const row = item as { n?: unknown; source?: unknown };
    const n = Number(row.n);
    const source = String(row.source ?? "").trim();
    return Number.isInteger(n) && source ? [{ n, source }] : [];
  });
  return sources.length ? sources : undefined;
}

/** The query a data answer can be rated against. Missing or junk ids are dropped. */
export function dataQueryMark(raw: unknown): DataQueryMark | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const row = raw as { log_id?: unknown; feedback?: unknown };
  const logId = Number(row.log_id);
  if (!Number.isInteger(logId) || logId <= 0) return undefined;
  const feedback = row.feedback === "good" || row.feedback === "bad" ? row.feedback : undefined;
  return { logId, feedback };
}

const SKIP_HISTORY = new Set(["(message)", "(confirm)"]);
const TAG_LABEL: Record<string, string> = {
  po: "PO",
  contract: "CONTRACT",
  report: "REPORT",
  brand: "BRAND",
  data: "DATA",
};

export function money(currency: string | null | undefined, n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  const formatted = n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${currency ?? ""} ${formatted}`.trim();
}

export function trimNum(n: number): string {
  if (Number.isInteger(n)) return String(n);
  return String(n);
}

export function shortDate(ts: string | null | undefined): string {
  if (!ts) return "—";
  return ts.slice(0, 10);
}

export function clip(text: string, n: number): string {
  const flat = text.replace(/\n/g, " ");
  return flat.length > n ? `${flat.slice(0, n)}…` : flat;
}

export function greeting(p: Pick<Partnership, "distributor_name" | "brand_name">): string {
  return `Hi — I can place an order for ${p.distributor_name} with ${p.brand_name}. What would you like, and how many cases?`;
}

export function tagLabel(tags: string[] | null | undefined): string {
  return (tags ?? []).filter(Boolean).map((t) => TAG_LABEL[t] ?? t.toUpperCase()).join(" · ");
}

export function chatTitle(
  session: { title?: string | null; history?: HistoryMessage[] },
  turns?: { role: string; text?: string }[],
): string {
  const titled = (session.title || "").trim();
  if (titled) return clip(titled, 40);
  for (const t of turns ?? []) {
    const text = (t.text || "").trim();
    if (t.role === "user" && text) return clip(text, 36);
  }
  for (const m of session.history ?? []) {
    const text = String(m.content || "").trim();
    if (m.role === "user" && text && !SKIP_HISTORY.has(text)) return clip(text, 36);
  }
  return "New chat";
}

export function confirmReady(steps: Step[] | null | undefined): boolean {
  const by = new Map((steps ?? []).map((s) => [s.title, s.status]));
  return by.get("Understand request") === "done" && by.get("Check inputs") === "done";
}

export function buildLedger(stage: string): Step[] {
  const check = stage === "gathering" ? "working" : "done";
  return [
    { title: "Understand request", status: "done" },
    { title: "Check inputs", status: check },
    { title: "Confirm draft", status: stage === "confirm" ? "needs-you" : stage === "submitted" ? "done" : "pending" },
    { title: "Create & submit order", status: stage === "submitted" ? "done" : "pending" },
  ];
}

export type DraftLine = {
  name: string;
  sku: string | null;
  reference: string;
  cases: number;
  case_price: number | null;
  line_total: number | null;
  moq_cases: number | null;
  ambiguous: boolean;
  unresolved: boolean;
  below_moq: boolean;
  missing_price: boolean;
  partial_layer: boolean;
  cases_to_full_layer: number | null;
};

export function lineFlag(line: DraftLine): string {
  if (line.ambiguous) return "which one?";
  if (line.unresolved) return "not found";
  if (line.below_moq) return `min ${line.moq_cases}`;
  if (line.missing_price) return "no price";
  if (line.partial_layer) return `+${line.cases_to_full_layer} = full layer`;
  return "";
}

export type Discount = { kind: string; value?: number | null; source?: string | null } | null;

export function discountText(discount: Discount, currency: string): string {
  if (!discount) return "not yet decided";
  let text = "none";
  if (discount.kind !== "none") {
    text = discount.kind === "percent" ? `${trimNum(discount.value ?? 0)}%` : money(currency, discount.value);
  }
  if (discount.source === "contract") text += " · per contract";
  return text;
}

export function lastMonth(today = new Date()): string {
  const first = new Date(today.getFullYear(), today.getMonth(), 1);
  const prev = new Date(first.getFullYear(), first.getMonth() - 1, 1);
  return `${prev.getFullYear()}-${String(prev.getMonth() + 1).padStart(2, "0")}`;
}

export type Fact = {
  fact_type: string;
  metric_key: string;
  numeric_value: number | null;
  currency: string | null;
  period_start: string | null;
  sku: string | null;
  entity: string | null;
  channel: string | null;
};

export function liveSummary(facts: Fact[], fallbackCurrency: string) {
  const sales = facts.filter((f) => f.fact_type === "sales");
  const revenue = sales.filter((f) => f.metric_key === "revenue");
  const units = sales.filter((f) => f.metric_key === "units_sold");
  const months = new Set(facts.map((f) => (f.period_start || "").slice(0, 7)).filter(Boolean));
  const skus = new Set(sales.map((f) => f.sku).filter(Boolean));
  const sum = (rows: Fact[]) => rows.reduce((acc, f) => acc + (f.numeric_value || 0), 0);
  return {
    currency: revenue.map((f) => f.currency).find(Boolean) || fallbackCurrency,
    revenue: revenue.length ? sum(revenue) : null,
    units: units.length ? sum(units) : null,
    months: months.size,
    skus: skus.size,
    revenueRows: revenue,
    unitRows: units,
    stockRows: facts.filter((f) => f.metric_key === "stock_on_hand"),
  };
}

export function sumBy(rows: Fact[], key: (f: Fact) => string): { label: string; value: number }[] {
  const map = new Map<string, number>();
  for (const f of rows) {
    const label = key(f);
    if (!label) continue;
    map.set(label, (map.get(label) || 0) + (f.numeric_value || 0));
  }
  return [...map.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([label, value]) => ({ label, value }));
}

export type OrderItem = { sku: string; quantity: number; case_pack: number | null; product_name: string };

export function orderedVsSold(orders: { items: OrderItem[] }[], units: Fact[]) {
  const ordered = new Map<string, number>();
  const names = new Map<string, string>();
  for (const order of orders) {
    for (const item of order.items) {
      ordered.set(item.sku, (ordered.get(item.sku) || 0) + item.quantity * (item.case_pack || 1));
      names.set(item.sku, item.product_name);
    }
  }
  const sold = new Map<string, number>();
  const entity = new Map<string, string>();
  for (const fact of units) {
    if (!fact.sku) continue;
    sold.set(fact.sku, (sold.get(fact.sku) || 0) + (fact.numeric_value || 0));
    if (fact.entity) entity.set(fact.sku, fact.entity);
  }
  if (ordered.size === 0 || units.length === 0) return [];
  return [...new Set([...ordered.keys(), ...sold.keys()])].sort().map((sku) => {
    const placed = ordered.get(sku) || 0;
    const moved = sold.get(sku) || 0;
    return {
      sku,
      product: names.get(sku) || entity.get(sku) || "",
      ordered: placed,
      sold: moved,
      sellThrough: placed ? moved / placed : null,
    };
  });
}

export function revenueLabel(summary: { currency: string; revenue: number | null }): string {
  if (summary.revenue == null) return "—";
  return `${summary.currency} ${summary.revenue.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

export function unitsLabel(n: number | null): string {
  if (n == null) return "—";
  return n.toLocaleString("en-US", { maximumFractionDigits: 0 });
}
