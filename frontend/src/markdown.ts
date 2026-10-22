/** Block parser for chat replies. Report drafts also use tables, links, and short paragraphs. */

export type Inline =
  | { kind: "text"; text: string }
  | { kind: "strong"; text: string }
  | { kind: "code"; text: string }
  | { kind: "link"; text: string; href: string };

export type Align = "left" | "center" | "right";

export type Block =
  | { kind: "heading"; level: number; inlines: Inline[] }
  | { kind: "paragraph"; lines: Inline[][] }
  | { kind: "list"; ordered: boolean; items: Inline[][] }
  | { kind: "table"; header: Inline[][]; aligns: Align[]; rows: Inline[][][] };

const HEADING = /^(#{1,6})[ \t]+(\S.*)$/;
const BULLET = /^[-*][ \t]+(\S.*)$/;
const ORDERED = /^\d+[.)][ \t]+(\S.*)$/;
const MARK = /\[([^\]\n]+)\]\(([^)\s]+)\)|\*\*([^*\n]+)\*\*|`([^`\n]+)`/g;
const TABLE_LINE = /^\s*\|/;
const LINK_LINE = /^\[[^\]\n]+\]\([^)\s]+\)\s*$/;

export function parseInline(text: string): Inline[] {
  const inlines: Inline[] = [];
  let cursor = 0;
  for (const match of text.matchAll(MARK)) {
    const index = match.index ?? 0;
    if (index > cursor) inlines.push({ kind: "text", text: text.slice(cursor, index) });
    if (match[1] !== undefined) inlines.push({ kind: "link", text: match[1], href: match[2] ?? "" });
    else if (match[3] !== undefined) inlines.push({ kind: "strong", text: match[3] });
    else inlines.push({ kind: "code", text: match[4] ?? "" });
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

function cellsOf(line: string): string[] | null {
  const trimmed = line.trim();
  if (!trimmed.includes("|")) return null;
  const body = trimmed.replace(/^\|/, "").replace(/\|$/, "");
  const cells = body.split("|").map((cell) => cell.trim());
  return cells.length >= 2 ? cells : null;
}

function isSeparator(line: string): boolean {
  const cells = cellsOf(line);
  return !!cells && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function isTableStart(lines: string[], index: number): boolean {
  return !!cellsOf(lines[index] ?? "") && isSeparator(lines[index + 1] ?? "");
}

function alignOf(cell: string): Align {
  const token = cell.trim();
  const left = token.startsWith(":");
  const right = token.endsWith(":");
  if (left && right) return "center";
  if (right) return "right";
  return "left";
}

/** Break a wall of sentences into short paragraphs. Tables, lists, headings, and lone links stay put. */
export function shapeProse(text: string): string {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: string[] = [];
  let prose: string[] = [];

  const flush = () => {
    if (prose.length === 0) return;
    const flat = prose.map((part) => part.trim()).join("");
    prose = [];
    const sentences = flat.split(/(?<=[。！？])/).map((item) => item.trim()).filter(Boolean);
    if (sentences.length === 0) {
      if (flat) blocks.push(flat);
      return;
    }
    if (sentences.length <= 2) {
      blocks.push(sentences.join(""));
      return;
    }
    for (let index = 0; index < sentences.length; index += 2) {
      blocks.push(sentences.slice(index, index + 2).join(""));
    }
  };

  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    const stripped = line.trim();
    if (!stripped) {
      flush();
      index += 1;
      continue;
    }
    if (TABLE_LINE.test(line)) {
      flush();
      const table = [line.trimEnd()];
      index += 1;
      while (index < lines.length && lines[index].trim() && TABLE_LINE.test(lines[index])) {
        table.push(lines[index].trimEnd());
        index += 1;
      }
      blocks.push(table.join("\n"));
      continue;
    }
    if (HEADING.test(stripped) || LINK_LINE.test(stripped)) {
      flush();
      blocks.push(stripped);
      index += 1;
      continue;
    }
    if (BULLET.test(stripped) || ORDERED.test(stripped)) {
      flush();
      const items = [stripped];
      index += 1;
      while (index < lines.length) {
        const next = lines[index].trim();
        if (!next) {
          let look = index + 1;
          while (look < lines.length && !lines[look].trim()) look += 1;
          if (BULLET.test(lines[look] ?? "") || ORDERED.test(lines[look] ?? "")) {
            index = look;
            continue;
          }
          break;
        }
        if (!BULLET.test(next) && !ORDERED.test(next)) break;
        items.push(next);
        index += 1;
      }
      blocks.push(items.join("\n"));
      continue;
    }
    prose.push(stripped);
    index += 1;
  }
  flush();
  return blocks.join("\n\n");
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
    if (isTableStart(lines, i)) {
      const header = cellsOf(lines[i]) ?? [];
      const aligns = (cellsOf(lines[i + 1]) ?? []).map(alignOf);
      i += 2;
      const rows: Inline[][][] = [];
      while (i < lines.length && cellsOf(lines[i]) && !isSeparator(lines[i]) && lines[i].trim()) {
        rows.push((cellsOf(lines[i]) ?? []).map((cell) => parseInline(cell)));
        i += 1;
      }
      blocks.push({
        kind: "table",
        header: header.map((cell) => parseInline(cell)),
        aligns,
        rows,
      });
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
      && !isTableStart(lines, i)
    ) {
      paragraph.push(parseInline(lines[i]));
      i += 1;
    }
    blocks.push({ kind: "paragraph", lines: paragraph });
  }
  return blocks;
}
