"""Split an agreement into a heading tree. Regex only — the model is not involved.

Clause boundaries in these files are the numbered headings (`第一条`, `1`, `1.1`, `1.1.1`),
not page breaks. A line that is a whole sentence, or a lettered item such as `(a)`, stays
in the parent body so a list inside a clause does not become its own section. Page numbers
are kept as coordinates for a later quote check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TITLE_MAX = 80

_ARTICLE = re.compile(r"^第\s*([0-9一二三四五六七八九十百零〇]+)\s*条\s*(.*)$")
_EN = re.compile(
    r"^(?:article|section|clause)\s+(\d{1,2}(?:\.\d{1,2})*)\s*[:.\-—–]*\s*(.*)$",
    re.IGNORECASE,
)
_DOTTED = re.compile(r"^(\d{1,2}(?:\.\d{1,2})*)(?:[.)）、]\s*|\s+)(.*)$")
_CN = re.compile(r"^([一二三四五六七八九十]+)[、.．]\s*(\S.*)$")
_HASH = re.compile(r"^#{1,6}\s+")


@dataclass
class _Node:
    number: str
    title: str
    level: int
    kind: str
    page_start: int
    body: list[tuple[int, str]] = field(default_factory=list)
    children: list[_Node] = field(default_factory=list)


@dataclass(frozen=True)
class _Hit:
    number: str
    title: str
    body_first: str
    level: int
    kind: str


def _clean(line: str) -> str:
    text = _HASH.sub("", line.strip())
    if text.startswith("**") and text.endswith("**") and len(text) > 4:
        text = text[2:-2].strip()
    return text


def _sentence(rest: str) -> bool:
    text = rest.strip()
    return len(text) > TITLE_MAX or text.endswith((".", "。", ";", "；"))


def _title_and_body(rest: str) -> tuple[str, str]:
    text = rest.strip()
    if not text or _sentence(text):
        return "", text
    return text, ""


def _inside_numbered(stack: list[_Node]) -> bool:
    return any(node.kind in {"article", "dotted", "en"} for node in stack)


def _article_level(stack: list[_Node]) -> int | None:
    for node in reversed(stack):
        if node.kind == "article":
            return node.level
    return None


def _match(line: str, stack: list[_Node]) -> _Hit | None:
    article = _ARTICLE.match(line)
    if article:
        title, body = _title_and_body(article.group(2))
        return _Hit(f"第{article.group(1)}条", title, body, 1, "article")
    english = _EN.match(line)
    if english:
        number = english.group(1)
        title, body = _title_and_body(english.group(2))
        return _Hit(number, title, body, max(1, number.count(".") + 1), "article")
    dotted = _DOTTED.match(line)
    if dotted:
        number = dotted.group(1)
        title, body = _title_and_body(dotted.group(2))
        depth = number.count(".") + 1
        base = _article_level(stack)
        level = base + depth if base is not None else depth
        return _Hit(number, title, body, level, "dotted")
    cn = _CN.match(line)
    if cn and not _inside_numbered(stack):
        title, body = _title_and_body(cn.group(2))
        if title:
            return _Hit(cn.group(1), title, body, 1, "cn")
    return None


def _add(stack: list[_Node], roots: list[_Node], hit: _Hit, page: int) -> None:
    while stack and stack[-1].level >= hit.level:
        stack.pop()
    node = _Node(hit.number, hit.title, hit.level, hit.kind, page)
    if hit.body_first:
        node.body.append((page, hit.body_first))
    if stack:
        stack[-1].children.append(node)
    else:
        roots.append(node)
    stack.append(node)


def _emit(nodes: list[_Node], parent_path: str, counter: list[int]) -> list[dict]:
    out: list[dict] = []
    for index, node in enumerate(nodes):
        counter[0] += 1
        path = node.number if not parent_path else f"{parent_path} / {node.number}"
        if not node.number:
            path = ""
        pages = [node.page_start, *[page for page, _ in node.body]]
        out.append({
            "id": f"c{counter[0]}",
            "number": node.number,
            "title": node.title,
            "body": "\n".join(text for _, text in node.body).strip(),
            "path": path,
            "page_start": node.page_start,
            "page_end": max(pages),
            "order": index,
            "children": _emit(node.children, path, counter),
        })
    return out


def split_chunks(pages: list[tuple[int, str]]) -> list[dict]:
    """Heading tree for one agreement. Each node is one JSON object; subheadings sit in `children`.

    Text before the first heading is a preamble node (`number` empty). Lettered items and
    long numbered sentences stay in `body`.
    """
    roots: list[_Node] = []
    stack: list[_Node] = []
    preamble: _Node | None = None
    for page, text in pages:
        for raw in (text or "").splitlines():
            line = _clean(raw)
            if not line:
                continue
            hit = _match(line, stack)
            if hit is not None:
                _add(stack, roots, hit, page)
                continue
            if stack:
                stack[-1].body.append((page, line))
                continue
            if preamble is None:
                preamble = _Node("", "", 0, "preamble", page)
                roots.insert(0, preamble)
            preamble.body.append((page, line))
    return _emit(roots, "", [0])
