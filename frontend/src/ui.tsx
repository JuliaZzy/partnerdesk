import type { ReactNode } from "react";

import type { ReportChart } from "./format";

const TONE: Record<string, string> = {
  active: "ok",
  approved: "ok",
  confirmed: "ok",
  submitted: "ok",
  distilled: "ok",
  completed: "ok",
  draft: "wait",
  uploaded: "wait",
  gathering: "wait",
  confirm: "wait",
  pending_approval: "wait",
  superseded: "mute",
  archived: "mute",
  failed: "bad",
  cancelled: "bad",
};

export function Badge({ status }: { status: string | null | undefined }) {
  return <span className={`badge ${TONE[status || ""] || "info"}`}>{status || "—"}</span>;
}

export function Metrics({ items }: { items: { label: string; value: string; hint?: string }[] }) {
  return (
    <div className="metrics">
      {items.map((item) => (
        <div className="metric" key={item.label} title={item.hint}>
          <div className="metric-value">{item.value}</div>
          <div className="metric-label">{item.label}</div>
        </div>
      ))}
    </div>
  );
}

export function Tabs({ labels, value, onChange }: { labels: string[]; value: string; onChange: (value: string) => void }) {
  return (
    <div className="tabs" role="tablist">
      {labels.map((label) => (
        <button
          key={label}
          type="button"
          role="tab"
          aria-selected={value === label}
          className={value === label ? "tab on" : "tab"}
          onClick={() => onChange(label)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

export function DataTable<T>({
  columns,
  rows,
}: {
  columns: { key: string; label: string; wrap?: boolean; render?: (row: T) => ReactNode }[];
  rows: T[];
}) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} className={column.wrap ? "wrap" : ""}>{column.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column.key} className={column.wrap ? "wrap" : ""}>
                  {column.render ? column.render(row) : formatCell((row as Record<string, unknown>)[column.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatCell(value: unknown): ReactNode {
  if (value == null || value === "") return "—";
  if (Array.isArray(value)) return value.join(", ") || "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function PageHead({ title, caption }: { title: string; caption?: string }) {
  return (
    <header className="page-head">
      <h1>{title}</h1>
      {caption && <p>{caption}</p>}
    </header>
  );
}

export function ReportFigure({ chart }: { chart: ReportChart }) {
  return (
    <figure className="chart-block">
      <figcaption>{chart.title}</figcaption>
      {chart.chartType === "line" && <LineFigure points={chart.points} />}
      {chart.chartType === "pie" && <PieFigure points={chart.points} />}
      {chart.chartType === "bar" && <ChartBars points={chart.points} />}
    </figure>
  );
}

const PIE_COLORS = ["#c4622d", "#2c6b4a", "#2c5270", "#8a5a12", "#9a4a1e", "#6f675e"];

function PieFigure({ points }: { points: { x: string; y: number }[] }) {
  const total = points.reduce((sum, point) => sum + point.y, 0);
  let cursor = 0;
  const stops = points.map((point, index) => {
    const start = cursor;
    cursor += total > 0 ? (point.y / total) * 100 : 0;
    return `${PIE_COLORS[index % PIE_COLORS.length]} ${start}% ${cursor}%`;
  });
  return (
    <div className="pie-row">
      <div className="pie" style={{ background: `conic-gradient(${stops.join(", ")})` }} />
      <ul className="chart-values">
        {points.map((point, index) => (
          <li key={`${point.x}-${index}`}>
            <span className="pie-swatch" style={{ background: PIE_COLORS[index % PIE_COLORS.length] }} />
            {point.x}: {point.y.toLocaleString("en-US")}
            {total > 0 ? ` · ${Math.round((point.y / total) * 1000) / 10}%` : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ChartBars({ points }: { points: { x: string; y: number }[] }) {
  const max = Math.max(1, ...points.map((point) => Math.abs(point.y)));
  return (
    <div className="bars">
      {points.map((point, index) => (
        <div className="bar-row" key={`${point.x}-${index}`}>
          <span>{point.x}</span>
          <div className="bar-track"><div className="bar-fill" style={{ width: `${(Math.abs(point.y) / max) * 100}%` }} /></div>
          <span>{point.y.toLocaleString("en-US")}</span>
        </div>
      ))}
    </div>
  );
}

function LineFigure({ points }: { points: { x: string; y: number }[] }) {
  const ys = points.map((point) => point.y);
  const min = Math.min(0, ...ys);
  const max = Math.max(0, ...ys);
  const span = Math.max(1e-9, max - min);
  const coords = points.map((point, index) => {
    const x = points.length === 1 ? 50 : (index / (points.length - 1)) * 100;
    const y = 36 - ((point.y - min) / span) * 32;
    return `${x},${y}`;
  }).join(" ");
  return (
    <>
      <svg className="chart-line" viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true">
        <polyline points={coords} fill="none" stroke="currentColor" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      </svg>
      <ul className="chart-values">
        {points.map((point, index) => (
          <li key={`${point.x}-${index}`}>{point.x}: {point.y.toLocaleString("en-US")}</li>
        ))}
      </ul>
    </>
  );
}

export function Bars({ rows }: { rows: { label: string; value: number }[] }) {
  const max = Math.max(1, ...rows.map((row) => row.value));
  return (
    <div className="bars">
      {rows.map((row) => (
        <div className="bar-row" key={row.label}>
          <span>{row.label}</span>
          <div className="bar-track"><div className="bar-fill" style={{ width: `${(row.value / max) * 100}%` }} /></div>
          <span>{row.value.toLocaleString("en-US")}</span>
        </div>
      ))}
    </div>
  );
}
