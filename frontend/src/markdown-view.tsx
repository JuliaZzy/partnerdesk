import type { ReactNode } from "react";

import type { ReportChart } from "./format";
import { parseMarkdown, shapeProse, type Inline } from "./markdown";
import { ReportFigure } from "./ui";

function renderInlines(parts: Inline[], keyPrefix: string, chartIds: Set<string>): ReactNode[] {
  return parts.map((part, index) => {
    const key = `${keyPrefix}-${index}`;
    if (part.kind === "strong") return <strong key={key}>{part.text}</strong>;
    if (part.kind === "code") return <code key={key}>{part.text}</code>;
    if (part.kind === "link") {
      if (chartIds.has(part.href) || !/^https?:\/\//i.test(part.href)) return <span key={key}>{part.text}</span>;
      return <a key={key} href={part.href} target="_blank" rel="noreferrer">{part.text}</a>;
    }
    return <span key={key}>{part.text}</span>;
  });
}

function chartsIn(lines: Inline[][], byId: Map<string, ReportChart>, used: Set<string>): ReportChart[] {
  const found: ReportChart[] = [];
  for (const line of lines) {
    for (const part of line) {
      if (part.kind !== "link" || used.has(part.href) || !byId.has(part.href)) continue;
      used.add(part.href);
      const chart = byId.get(part.href);
      if (chart) found.push(chart);
    }
  }
  return found;
}

function chartOnly(lines: Inline[][], chartIds: Set<string>): boolean {
  const parts = lines.flat().filter((part) => part.kind !== "text" || part.text.trim());
  return parts.length > 0 && parts.every((part) => part.kind === "link" && chartIds.has(part.href));
}

/** Assistant text, including a report draft, drawn as headings, tables, and charts. */
export function Markdown({ text, charts, prose }: { text: string; charts?: ReportChart[]; prose?: boolean }) {
  const blocks = parseMarkdown(prose ? shapeProse(text) : text);
  const byId = new Map<string, ReportChart>();
  for (const chart of charts ?? []) {
    if (!byId.has(chart.id)) byId.set(chart.id, chart);
  }
  const chartIds = new Set(byId.keys());
  const used = new Set<string>();
  if (blocks.length === 0 && byId.size === 0) return null;
  const figures = (found: ReportChart[]) => found.map((chart) => <ReportFigure key={chart.id} chart={chart} />);
  return (
    <div className={prose ? "md prose" : "md"}>
      {blocks.map((block, index) => {
        if (block.kind === "heading") {
          const level = Math.min(Math.max(block.level, 1), 3) as 1 | 2 | 3;
          const Tag = (["h1", "h2", "h3"] as const)[level - 1];
          const found = chartsIn([block.inlines], byId, used);
          return (
            <div key={index}>
              <Tag>{renderInlines(block.inlines, String(index), chartIds)}</Tag>
              {figures(found)}
            </div>
          );
        }
        if (block.kind === "list") {
          const Tag = block.ordered ? "ol" : "ul";
          const found = chartsIn(block.items, byId, used);
          return (
            <div key={index}>
              <Tag>
                {block.items.map((item, itemIndex) => (
                  <li key={itemIndex}>{renderInlines(item, `${index}-${itemIndex}`, chartIds)}</li>
                ))}
              </Tag>
              {figures(found)}
            </div>
          );
        }
        if (block.kind === "table") {
          return (
            <div className="table-wrap" key={index}>
              <table>
                <thead>
                  <tr>
                    {block.header.map((cell, cellIndex) => (
                      <th key={cellIndex} style={{ textAlign: block.aligns[cellIndex] || "left" }}>
                        {renderInlines(cell, `${index}-h-${cellIndex}`, chartIds)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {block.rows.map((row, rowIndex) => (
                    <tr key={rowIndex}>
                      {block.header.map((_, cellIndex) => (
                        <td key={cellIndex} style={{ textAlign: block.aligns[cellIndex] || "left" }}>
                          {renderInlines(row[cellIndex] ?? [], `${index}-${rowIndex}-${cellIndex}`, chartIds)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }
        const found = chartsIn(block.lines, byId, used);
        if (chartOnly(block.lines, chartIds)) return <div key={index}>{figures(found)}</div>;
        return (
          <div key={index}>
            <p>
              {block.lines.map((line, lineIndex) => (
                <span key={lineIndex}>
                  {lineIndex > 0 && <br />}
                  {renderInlines(line, `${index}-${lineIndex}`, chartIds)}
                </span>
              ))}
            </p>
            {figures(found)}
          </div>
        );
      })}
      {figures((charts ?? []).filter((chart) => !used.has(chart.id)))}
    </div>
  );
}
