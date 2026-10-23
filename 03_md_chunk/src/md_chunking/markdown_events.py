from __future__ import annotations

import re
from dataclasses import asdict, dataclass


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
CHAPTER_RE = re.compile(r"第\s*[0-9一二三四五六七八九十百]+\s*章")
TABLE_OPEN_RE = re.compile(r"<table(?:\s|>)", re.I)
TABLE_CLOSE_RE = re.compile(r"</table>", re.I)


@dataclass
class Event:
    event_id: int
    event_type: str
    line_start: int
    line_end: int
    raw_text: str
    chapter_heading: str | None = None
    section_heading: str | None = None
    heading_level: int | None = None
    heading_text: str | None = None
    table_index: int | None = None

    def to_dict(self):
        return asdict(self)


class UnclosedTableError(ValueError):
    pass


def parse_markdown(markdown: str) -> tuple[list[Event], dict]:
    lines = markdown.splitlines()
    events: list[Event] = []
    current_chapter = None
    current_heading = None
    table_index = 0
    assigned: set[int] = set()
    i = 0

    def add(kind: str, start: int, end: int, raw: str, **kwargs):
        nonlocal events
        events.append(Event(len(events) + 1, kind, start, end, raw,
                            current_chapter, current_heading, **kwargs))
        assigned.update(range(start, end + 1))

    while i < len(lines):
        line = lines[i]
        line_no = i + 1
        if not line.strip():
            assigned.add(line_no)
            i += 1
            continue
        heading = HEADING_RE.match(line)
        if heading:
            text = heading.group(2).strip()
            if CHAPTER_RE.search(text):
                current_chapter = text
            current_heading = text
            add("HEADING", line_no, line_no, line,
                heading_level=len(heading.group(1)), heading_text=text)
            i += 1
            continue
        if TABLE_OPEN_RE.search(line):
            start = i
            depth = 0
            found_close = False
            while i < len(lines):
                depth += len(TABLE_OPEN_RE.findall(lines[i]))
                depth -= len(TABLE_CLOSE_RE.findall(lines[i]))
                if depth <= 0:
                    found_close = True
                    break
                i += 1
            if not found_close:
                raise UnclosedTableError(f"Unclosed HTML table starting at line {start + 1}")
            table_index += 1
            add("TABLE", start + 1, i + 1, "\n".join(lines[start:i + 1]),
                table_index=table_index)
            i += 1
            continue
        start = i
        paragraph = []
        while i < len(lines):
            candidate = lines[i]
            if not candidate.strip() or HEADING_RE.match(candidate) or TABLE_OPEN_RE.search(candidate):
                break
            paragraph.append(candidate)
            i += 1
        add("TEXT", start + 1, start + len(paragraph), "\n".join(paragraph))

    nonblank = {n for n, line in enumerate(lines, 1) if line.strip()}
    return events, {
        "input_lines": len(lines),
        "nonblank_unassigned_lines": sorted(nonblank - assigned),
        "unclosed_table_count": 0,
    }
