import type { ReactNode } from "react";

import { parseMarkdown, type Inline } from "./markdown";

function renderInlines(parts: Inline[], keyPrefix: string): ReactNode[] {
  return parts.map((part, index) => {
    const key = `${keyPrefix}-${index}`;
    if (part.kind === "strong") return <strong key={key}>{part.text}</strong>;
    if (part.kind === "code") return <code key={key}>{part.text}</code>;
    return <span key={key}>{part.text}</span>;
  });
}

/** Assistant text, including a report draft, drawn as headings and lists. */
export function Markdown({ text }: { text: string }) {
  const blocks = parseMarkdown(text);
  if (blocks.length === 0) return null;
  return (
    <div className="md">
      {blocks.map((block, index) => {
        if (block.kind === "heading") {
          const level = Math.min(Math.max(block.level, 1), 3) as 1 | 2 | 3;
          const Tag = (["h1", "h2", "h3"] as const)[level - 1];
          return <Tag key={index}>{renderInlines(block.inlines, String(index))}</Tag>;
        }
        if (block.kind === "list") {
          const Tag = block.ordered ? "ol" : "ul";
          return (
            <Tag key={index}>
              {block.items.map((item, itemIndex) => (
                <li key={itemIndex}>{renderInlines(item, `${index}-${itemIndex}`)}</li>
              ))}
            </Tag>
          );
        }
        return (
          <p key={index}>
            {block.lines.map((line, lineIndex) => (
              <span key={lineIndex}>
                {lineIndex > 0 && <br />}
                {renderInlines(line, `${index}-${lineIndex}`)}
              </span>
            ))}
          </p>
        );
      })}
    </div>
  );
}
