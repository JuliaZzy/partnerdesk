"""Split brand-document pages into retrieval chunks.

Pages are GLM-OCR text in reading order. A page is a coordinate, not a boundary:
a section runs on until the next heading, including onto the next page. Blocks
come from blank lines. A heading ends the current chunk and pushes a section
path. Body text accumulates into a paragraph, and a paragraph stays in one
chunk unless that paragraph alone is over the cap. Tables are a separate kind
of chunk. Code fences are not detected; brand manuals are headings, prose, and
tables.

The section path is written at the top of the chunk text. The vector field has
no section column, and a later piece of a long section still has to name where
it sits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# A chunk holds a paragraph or two. The cap breaks between paragraphs.
CHUNK_CHARS = 500

# A numbered line or a colon line is a heading only when it is short. A longer
# line that happens to start with "1." is a sentence and stays in the paragraph.
_HEADING_CHARS = 20

_HASH = re.compile(r"^(#{1,6})\s+(\S.*)$")
_DOTTED = re.compile(r"^(\d{1,2}(?:\.\d{1,2})*)(?:[.)）、、]\s*|\s+)(\S.*)$")
_CN = re.compile(r"^([一二三四五六七八九十百零]+)[、.．]\s*(\S.*)$")
_BLANK = re.compile(r"\n\s*\n")
_END = ("。", "！", "？", ".", "!", "?")


def chunks_from_pages(doc_id: str, pages: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """One document's pages → chunks. `page` is the page the chunk starts on."""
    chunks = _Cursor().feed(pages)
    for i, chunk in enumerate(chunks):
        chunk["doc"] = doc_id
        chunk["cid"] = f"{doc_id}#p{chunk['page']}-{i}"
    return chunks


@dataclass(frozen=True)
class _Heading:
    level: int
    label: str


def _as_heading(line: str) -> _Heading | None:
    """A heading is a markdown line, a short numbered line, or a short colon line."""
    raw = line.strip()
    hashes = 0
    body = raw
    marked = _HASH.match(raw)
    if marked:
        hashes = len(marked.group(1))
        body = marked.group(2).strip()
    if not body or "|" in body or "\t" in body:
        return None
    level = _number_level(body)
    if level is not None and len(body) < _HEADING_CHARS:
        return _Heading(level, _label(body))
    if body.endswith((":", "：")) and len(body) < _HEADING_CHARS:
        return _Heading(hashes or 1, _label(body))
    if hashes:
        return _Heading(hashes, _label(body))
    return None


def _number_level(body: str) -> int | None:
    dotted = _DOTTED.match(body.replace("．", "."))
    if dotted:
        return dotted.group(1).count(".") + 1
    if _CN.match(body):
        return 1
    return None


def _label(body: str) -> str:
    stripped = body.strip().rstrip(":：").strip()
    return stripped or body.strip()


def _is_table_line(line: str) -> bool:
    return "|" in line or "\t" in line or "  " in line


def _closed(text: str) -> bool:
    return text.rstrip().endswith(_END)


def _continue(left: str, right: str) -> str:
    """Join a wrapped line. English words take a space; Chinese wraps do not."""
    a, b = left[-1], right[0]
    if a.isascii() and b.isascii() and a.isalnum() and b.isalnum():
        return f"{left} {right}"
    return left + right


def _render(section: str, body: str) -> str:
    body = body.strip()
    if section:
        return f"{section}\n\n{body}"
    return body


def _budget(section: str) -> int:
    extra = len(section) + 2 if section else 0
    if extra >= CHUNK_CHARS:
        return CHUNK_CHARS
    return CHUNK_CHARS - extra


def _split_prose(text: str, budget: int) -> list[str]:
    """Split one over-long paragraph on sentence ends, then on the cap."""
    if len(text) <= budget:
        return [text]
    parts: list[str] = []
    buf = ""
    for sent in _sentences(text):
        if len(sent) > budget:
            if buf:
                parts.append(buf)
                buf = ""
            parts.extend(sent[i:i + budget] for i in range(0, len(sent), budget))
            continue
        if buf and len(buf) + len(sent) > budget:
            parts.append(buf)
            buf = sent
        else:
            buf += sent
    if buf:
        parts.append(buf)
    return parts


def _sentences(text: str) -> list[str]:
    parts: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in _END:
            parts.append(buf)
            buf = ""
    if buf:
        parts.append(buf)
    return parts


def _split_table(table: str, budget: int) -> list[str]:
    """Break a long table on rows. Each piece repeats the header row."""
    lines = [line.strip() for line in table.splitlines() if line.strip()]
    if len(lines) < 2 or len(lines[0]) > budget:
        return _split_prose(table, budget)
    header, rows = lines[0], lines[1:]
    pieces: list[str] = []
    buf = header
    for row in rows:
        trial = f"{buf}\n{row}"
        if buf != header and len(trial) > budget:
            pieces.append(buf)
            trial = f"{header}\n{row}"
        buf = trial
    if buf != header or not pieces:
        pieces.append(buf)
    return pieces


class _Cursor:
    def __init__(self) -> None:
        self.hierarchy: list[str] = []
        self.pending = ""
        self.pending_page = 0
        self.body = ""
        self.page = 0
        self.section = ""
        self.out: list[dict[str, Any]] = []

    def feed(self, pages: list[tuple[int, str]]) -> list[dict[str, Any]]:
        for kind, text, page, level in _events(pages):
            if kind == "heading":
                self._finish_text()
                self._flush()
                del self.hierarchy[level - 1:]
                self.hierarchy.append(text)
            elif kind == "table":
                self._finish_text()
                self._flush()
                for piece in _split_table(text, _budget(self.path)):
                    self.out.append({"page": page, "text": _render(self.path, piece)})
            else:
                self._text(text, page)
        self._finish_text()
        self._flush()
        return self.out

    @property
    def path(self) -> str:
        return " > ".join(self.hierarchy)

    def _text(self, line: str, page: int) -> None:
        if self.pending and _closed(self.pending):
            self._emit_paragraph(self.pending, self.pending_page)
            self.pending = ""
        if not self.pending:
            self.pending = line
            self.pending_page = page
        else:
            self.pending = _continue(self.pending, line)

    def _finish_text(self) -> None:
        if self.pending:
            self._emit_paragraph(self.pending, self.pending_page)
            self.pending = ""

    def _emit_paragraph(self, para: str, page: int) -> None:
        para = para.strip()
        if not para:
            return
        if self.body and len(_render(self.section, f"{self.body}\n{para}")) > CHUNK_CHARS:
            self._flush()
        if not self.body and len(_render(self.path, para)) > CHUNK_CHARS:
            for piece in _split_prose(para, _budget(self.path)):
                self.out.append({"page": page, "text": _render(self.path, piece)})
            return
        if not self.body:
            self.body = para
            self.page = page
            self.section = self.path
            return
        self.body = f"{self.body}\n{para}"

    def _flush(self) -> None:
        if self.body.strip():
            self.out.append({"page": self.page, "text": _render(self.section, self.body)})
        self.body = ""


def _events(pages: list[tuple[int, str]]):
    events: list[tuple[str, str, int, int]] = []
    table: list[str] = []
    table_page = 0

    def flush_table() -> None:
        nonlocal table_page
        if table:
            events.append(("table", "\n".join(table), table_page, 0))
            table.clear()

    for page, text in pages:
        normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        for para in _BLANK.split(normalized.strip()):
            for line in para.splitlines():
                line = line.strip()
                if not line:
                    continue
                if "|" in line or "\t" in line:
                    if not table:
                        table_page = page
                    table.append(line)
                    continue
                heading = _as_heading(line)
                if heading is not None:
                    flush_table()
                    events.append(("heading", heading.label, page, heading.level))
                    continue
                if _is_table_line(line):
                    if not table:
                        table_page = page
                    table.append(line)
                    continue
                flush_table()
                events.append(("text", line, page, 0))
            flush_table()
    flush_table()
    return events
