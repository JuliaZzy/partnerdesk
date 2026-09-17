"""
Chunking + optional section detection on NormalizedDocument.

Environment (optional):
  CONTRACT_CHUNK_TARGET_CHARS — max chars per chunk (default 2000)
  CONTRACT_CHUNK_OVERLAP_CHARS — overlap between consecutive chunks (default 200)
  CONTRACT_CHUNK_BREAK_WINDOW — when a chunk must shrink, search this many chars
    from the end for a break: small-heading line first, then \\n\\n / \\n / space (default 300)
  CONTRACT_ENABLE_SECTIONS — if "0" / "false", skip section detection (default on)
"""

from __future__ import annotations

import os
import re
from typing import Optional

from ..schemas import DerivedArtifacts, NormalizedChunk, NormalizedDocument, NormalizedSection


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(0, int(raw.strip()))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


# --- Section heading heuristics (contracts EN/CN) ---

_HEADING_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"^\s*((Article|ARTICLE)\s+[IVXLC\d]+[\s.:]|[\s]*Article\s+\d+)",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*第[一二三四五六七八九十百千零〇\d]+[章节条款编]\s*[.\s:：]"),
    re.compile(r"^\s*\d+\.\d+(\.\d+)?\s+\S"),  # 1.1 Something
    re.compile(r"^\s*\d+\.\s+[A-Z][A-Za-z]"),  # 1. Title
    re.compile(r"^\s*((Schedule|SCHEDULE|Exhibit|EXHIBIT|Appendix|APPENDIX)\s+[A-Z0-9]?)"),
    re.compile(r"^\s*[IVX]+\.\s+\S"),  # IV. Title
    re.compile(r"^\s*\([a-z]\)\s+\S", re.IGNORECASE),  # (a) Subclause
]


def _is_heading_line(line: str) -> bool:
    s = line.strip()
    if len(s) < 3 or len(s) > 300:
        return False
    for pat in _HEADING_PATTERNS:
        if pat.match(s):
            return True
    # Short ALL CAPS line (title-like), not a full sentence
    if len(s) < 80 and s.isupper() and len(s.split()) <= 12:
        return True
    return False


def _guess_level(line: str) -> int:
    s = line.strip()
    if re.match(r"^\s*\d+\.\d+", s):
        return 2
    if re.match(r"^\s*\([a-z]\)", s, re.IGNORECASE):
        return 3
    return 1


def _linearize_pages(doc: NormalizedDocument) -> tuple[str, list[tuple[int, int, int]]]:
    """
    Build one string (same spirit as page separators) and spans (char_start, char_end, page_number).
    """
    sep = "\n\n"
    parts: list[str] = []
    offsets: list[tuple[int, int, int]] = []
    pos = 0
    for p in doc.pages:
        txt = p.page_text()
        if not txt.strip():
            continue
        if parts:
            pos += len(sep)
        start = pos
        pos += len(txt)
        offsets.append((start, pos, p.page_number))
        parts.append(txt)
    return (sep.join(parts), offsets)


def _char_to_page_range(ch: int, offsets: list[tuple[int, int, int]]) -> tuple[int, int]:
    """Map single char index to page (best effort)."""
    if not offsets:
        return 1, 1
    page = 1
    for s, e, pn in offsets:
        if s <= ch < e:
            return pn, pn
        if ch >= s:
            page = pn
    return page, page


def _span_to_page_range(a: int, b: int, offsets: list[tuple[int, int, int]]) -> tuple[int, int]:
    """Map [a, b) to min/max page overlapping that span."""
    pages: list[int] = []
    for s, e, pn in offsets:
        if e <= a or s >= b:
            continue
        pages.append(pn)
    if not pages:
        return _char_to_page_range(a, offsets)
    return min(pages), max(pages)


def detect_sections(linear_text: str, offsets: list[tuple[int, int, int]]) -> list[NormalizedSection]:
    """Heading-based sections with char ranges in linear_text."""
    if not linear_text.strip():
        return []

    lines: list[tuple[int, str]] = []
    pos = 0
    for line in linear_text.split("\n"):
        lines.append((pos, line))
        pos += len(line) + 1

    heading_starts: list[tuple[int, str, int]] = []
    for line_start, line in lines:
        if _is_heading_line(line):
            title = line.strip()[:500]
            heading_starts.append((line_start, title, _guess_level(line)))

    if not heading_starts:
        return []

    sections: list[NormalizedSection] = []
    n = len(linear_text)
    for i, (char_start, title, level) in enumerate(heading_starts):
        char_end = heading_starts[i + 1][0] if i + 1 < len(heading_starts) else n
        ps, pe = _span_to_page_range(char_start, char_end, offsets)
        sections.append(
            NormalizedSection(
                section_index=i,
                title=title,
                level=level,
                page_start=ps,
                page_end=pe,
                char_start=char_start,
                char_end=char_end,
            )
        )
    return sections


def _section_hint_for_chunk(
    chunk_start: int,
    chunk_end: int,
    sections: list[NormalizedSection],
) -> Optional[str]:
    """Title of first section overlapping [chunk_start, chunk_end)."""
    for sec in sections:
        if sec.char_start is None or sec.char_end is None:
            continue
        if sec.char_end <= chunk_start or sec.char_start >= chunk_end:
            continue
        return sec.title or None
    return None


def _segment_ranges(sections: list[NormalizedSection], n: int) -> list[tuple[int, int]]:
    """Contiguous (start, end) spans: preamble before first heading, then each section."""
    if not sections:
        return [(0, n)]
    out: list[tuple[int, int]] = []
    first = sections[0]
    if first.char_start is not None and first.char_start > 0:
        out.append((0, first.char_start))
    for sec in sections:
        if sec.char_start is None or sec.char_end is None:
            continue
        out.append((sec.char_start, sec.char_end))
    return out


def _find_break_point(
    linear_text: str,
    chunk_start: int,
    cut_end: int,
    window_back: int,
) -> int:
    """
    Prefer breaking before a small-heading line, else paragraph/line/space, inside the last
    `window_back` chars before `cut_end`. Returns exclusive end index for [chunk_start, end).
    """
    if cut_end <= chunk_start + 1:
        return cut_end
    win_start = max(chunk_start, cut_end - window_back)
    sub = linear_text[win_start:cut_end]
    idx = win_start
    line_ranges: list[tuple[int, str]] = []
    for line in sub.split("\n"):
        line_ranges.append((idx, line))
        idx += len(line) + 1
    for line_start, line in reversed(line_ranges):
        if _is_heading_line(line) and line_start > chunk_start:
            return line_start
    window = linear_text[win_start:cut_end]
    for sep in ("\n\n", "\n", " "):
        cut = window.rfind(sep)
        if cut != -1:
            return max(chunk_start + 1, win_start + cut + len(sep))
    return cut_end


def _split_long_span_ranges(
    linear_text: str,
    span_start: int,
    span_end: int,
    *,
    max_chars: int,
    overlap: int,
    break_window: int,
    prev_chunk_end: Optional[int],
) -> list[tuple[int, int]]:
    """Split [span_start, span_end) into overlapping chunks with paragraph/heading-aware breaks."""
    out: list[tuple[int, int]] = []
    start = span_start
    if prev_chunk_end is not None:
        start = max(span_start, prev_chunk_end - overlap)
    while start < span_end:
        end = min(start + max_chars, span_end)
        if end < span_end:
            end = _find_break_point(linear_text, start, end, break_window)
        if end <= start:
            end = min(start + max_chars, span_end)
        if end <= start:
            break
        out.append((start, end))
        if end >= span_end:
            break
        nxt = max(0, end - overlap)
        if nxt >= end:
            nxt = end
        start = nxt
    return out


def build_chunks(
    linear_text: str,
    offsets: list[tuple[int, int, int]],
    sections: list[NormalizedSection],
    *,
    max_chars: int,
    overlap: int,
) -> list[NormalizedChunk]:
    if max_chars < 200:
        max_chars = 200
    if overlap >= max_chars:
        overlap = max(0, max_chars // 10)

    break_window = _env_int("CONTRACT_CHUNK_BREAK_WINDOW", 300)

    n = len(linear_text)
    if n == 0:
        return []

    # Ordered (start, end) spans with overlap already applied where needed
    ranges_out: list[tuple[int, int]] = []
    prev_end: Optional[int] = None

    if not sections:
        ranges_out = _split_long_span_ranges(
            linear_text,
            0,
            n,
            max_chars=max_chars,
            overlap=overlap,
            break_window=break_window,
            prev_chunk_end=None,
        )
    else:
        segments = _segment_ranges(sections, n)
        i = 0
        while i < len(segments):
            seg_start = segments[i][0]
            seg_end = segments[i][1]
            j = i + 1
            while j < len(segments) and segments[j][1] - seg_start <= max_chars:
                seg_end = segments[j][1]
                j += 1
            span_len = seg_end - seg_start
            if span_len <= max_chars:
                cs = seg_start if prev_end is None else max(seg_start, prev_end - overlap)
                ranges_out.append((cs, seg_end))
                prev_end = seg_end
                i = j
            else:
                subs = _split_long_span_ranges(
                    linear_text,
                    seg_start,
                    seg_end,
                    max_chars=max_chars,
                    overlap=overlap,
                    break_window=break_window,
                    prev_chunk_end=prev_end,
                )
                ranges_out.extend(subs)
                if subs:
                    prev_end = subs[-1][1]
                i = j

    chunks: list[NormalizedChunk] = []
    idx = 0
    for start, end in ranges_out:
        if end <= start:
            continue
        piece = linear_text[start:end].strip()
        if not piece:
            continue
        ps, pe = _span_to_page_range(start, end, offsets)
        hint = _section_hint_for_chunk(start, end, sections)
        chunks.append(
            NormalizedChunk(
                chunk_index=idx,
                text=piece,
                page_start=ps,
                page_end=pe,
                section_hint=hint,
                char_start=start,
                char_end=end,
            )
        )
        idx += 1

    return chunks


def enrich_normalized_document(
    doc: NormalizedDocument,
    *,
    enable_sections: Optional[bool] = None,
    target_chars: Optional[int] = None,
    overlap_chars: Optional[int] = None,
) -> NormalizedDocument:
    """
    Fill `derived.sections` (heuristic) and `derived.chunks` (sliding window).

    Does not mutate the input document in place; returns a copy with updated `derived`.
    """
    if enable_sections is None:
        enable_sections = _env_bool("CONTRACT_ENABLE_SECTIONS", True)

    max_chars = target_chars if target_chars is not None else _env_int("CONTRACT_CHUNK_TARGET_CHARS", 2000)
    overlap = overlap_chars if overlap_chars is not None else _env_int("CONTRACT_CHUNK_OVERLAP_CHARS", 200)

    linear_text, offsets = _linearize_pages(doc)
    if not linear_text.strip():
        return doc.model_copy(
            update={"derived": DerivedArtifacts(sections=[], chunks=[])}
        )

    sections: list[NormalizedSection] = []
    if enable_sections:
        sections = detect_sections(linear_text, offsets)

    chunks = build_chunks(linear_text, offsets, sections, max_chars=max_chars, overlap=overlap)

    return doc.model_copy(
        update={"derived": DerivedArtifacts(sections=sections, chunks=chunks)}
    )
