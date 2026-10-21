/** Block parser for chat replies. Report drafts use headings and lists; everything else stays text. */

export type Inline =
  | { kind: "text"; text: string }
  | { kind: "strong"; text: string }
  | { kind: "code"; text: string };

export type Block =
  | { kind: "heading"; level: number; inlines: Inline[] }
  | { kind: "paragraph"; lines: Inline[][] }
  | { kind: "list"; ordered: boolean; items: Inline[][] };

const HEADING = /^(#{1,6})[ \t]+(\S.*)$/;
const BULLET = /^[-*][ \t]+(\S.*)$/;
const ORDERED = /^\d+[.)][ \t]+(\S.*)$/;
const MARK = /\*\*([^*\n]+)\*\*|`([^`\n]+)`/g;

export function parseInline(text: string): Inline[] {
  const inlines: Inline[] = [];
  let cursor = 0;
  for (const match of text.matchAll(MARK)) {
    const index = match.index ?? 0;
    if (index > cursor) inlines.push({ kind: "text", text: text.slice(cursor, index) });
    if (match[1] !== undefined) inlines.push({ kind: "strong", text: match[1] });
    else inlines.push({ kind: "code", text: match[2] ?? "" });
    cursor = index + match[0].length;
  }
  if (cursor < text.length) inlines.push({ kind: "text", text: text.slice(cursor) });
  if (inlines.length === 0) inlines.push({ kind: "text", text });
  return inlines;
}

function listItem(line: string, ordered: boolean): string | null {
  const match = (ordered ? ORDERED : BULLET).exec(line);
  return match ? match[1] : null;
}

export function parseMarkdown(source: string): Block[] {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    if (lines[i].trim() === "") {
      i += 1;
      continue;
    }
    const heading = HEADING.exec(lines[i]);
    if (heading) {
      blocks.push({ kind: "heading", level: heading[1].length, inlines: parseInline(heading[2].trim()) });
      i += 1;
      continue;
    }
    const ordered = ORDERED.test(lines[i]);
    if (ordered || BULLET.test(lines[i])) {
      const items: Inline[][] = [];
      while (i < lines.length) {
        if (lines[i].trim() === "") {
          let next = i + 1;
          while (next < lines.length && lines[next].trim() === "") next += 1;
          if (listItem(lines[next] ?? "", ordered) === null) break;
          i = next;
          continue;
        }
        const item = listItem(lines[i], ordered);
        if (item === null) break;
        items.push(parseInline(item.trim()));
        i += 1;
      }
      blocks.push({ kind: "list", ordered, items });
      continue;
    }
    const paragraph: Inline[][] = [];
    while (
      i < lines.length
      && lines[i].trim() !== ""
      && !HEADING.test(lines[i])
      && !BULLET.test(lines[i])
      && !ORDERED.test(lines[i])
    ) {
      paragraph.push(parseInline(lines[i]));
      i += 1;
    }
    blocks.push({ kind: "paragraph", lines: paragraph });
  }
  return blocks;
}
