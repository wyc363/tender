from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .markdown_events import Event
from .table_parser import ParsedTable, parse_html_table, serialize_row


CLAUSE_RE = re.compile(r"(?m)^\s*(\d{1,3}(?:\.\d{1,3})+)[\.．、]?\s*")
REFERENCE_RE = re.compile(r"(?:详见|参见).{0,40}|按[^\n。；]{0,30}第[^\n。；]{0,20}[条项]")
AMENDMENT_WORDS = ("补正", "修改表", "更正表", "澄清表")
STRONG_END = set("。！？!?")
WEAK_END = set("；;\n")


@dataclass(frozen=True)
class Limits:
    target: int = 1000
    soft_max: int = 1400
    hard_max: int = 1800
    minimum: int = 300

    def validate(self):
        if not (0 < self.minimum <= self.target <= self.soft_max <= self.hard_max):
            raise ValueError("Require 0 < minimum <= target <= soft_max <= hard_max")


def ordered_unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def clause_refs(text: str) -> list[str]:
    return ordered_unique(match.group(1) for match in CLAUSE_RE.finditer(text))


def reference_candidates(text: str) -> list[str]:
    return ordered_unique(" ".join(m.group(0).split()) for m in REFERENCE_RE.finditer(text))


def natural_split(text: str, target: int, hard_max: int) -> tuple[list[str], bool]:
    """Split at nearby punctuation; return whether a hard split was required."""
    text = text.strip()
    if len(text) <= hard_max:
        return [text], False
    parts, forced = [], False
    while len(text) > hard_max:
        low = max(1, min(target, len(text)) // 2)
        high = min(hard_max, len(text) - 1)
        strong = [i + 1 for i in range(low, high) if text[i] in STRONG_END]
        weak = [i + 1 for i in range(low, high) if text[i] in WEAK_END]
        options = strong or weak
        if options:
            cut = min(options, key=lambda pos: abs(pos - target))
        else:
            cut = min(hard_max, len(text) - 1)
            forced = True
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts, forced


def _base_chunk(event: Event, chunk_type: str, text: str) -> dict:
    return {
        "chunk_type": chunk_type,
        "source_line_start": event.line_start,
        "source_line_end": event.line_end,
        "chapter_heading": event.chapter_heading,
        "section_heading": event.section_heading,
        "text": text,
        "char_count": len(text),
        "clause_refs": clause_refs(text),
        "reference_candidates": reference_candidates(text),
        "special_type": None,
        "quality_flags": [],
    }


def _split_text_event(event: Event, limits: Limits) -> list[dict]:
    parts, forced = natural_split(event.raw_text, limits.target, limits.hard_max)
    chunks = [_base_chunk(event, "TEXT", part) for part in parts if part]
    if forced:
        for chunk in chunks:
            chunk["quality_flags"].append("FORCED_TEXT_SPLIT")
    return chunks


def build_text_region(events: list[Event], limits: Limits) -> list[dict]:
    if not events:
        return []
    total_text = "\n\n".join(event.raw_text.strip() for event in events if event.raw_text.strip())
    if len(total_text) <= limits.soft_max:
        chunk = _base_chunk(events[0], "TEXT", total_text)
        chunk["source_line_end"] = events[-1].line_end
        return [chunk] if total_text else []
    units: list[dict] = []
    for event in events:
        units.extend(_split_text_event(event, limits))
    output: list[dict] = []
    current: list[dict] = []

    def combined(items):
        return "\n\n".join(item["text"] for item in items)

    def close():
        nonlocal current
        if not current:
            return
        text = combined(current)
        chunk = _base_chunk(events[0], "TEXT", text)
        chunk["source_line_start"] = current[0]["source_line_start"]
        chunk["source_line_end"] = current[-1]["source_line_end"]
        chunk["quality_flags"] = ordered_unique(flag for item in current for flag in item["quality_flags"])
        output.append(chunk)
        current = []

    for unit in units:
        candidate = combined(current + [unit])
        if not current or len(candidate) <= limits.target:
            current.append(unit)
        elif len(combined(current)) >= 600:
            close()
            current.append(unit)
        elif len(candidate) <= limits.hard_max:
            current.append(unit)
        else:
            close()
            current.append(unit)
    close()
    if len(output) > 1 and output[-1]["char_count"] < limits.minimum:
        merged = output[-2]["text"] + "\n\n" + output[-1]["text"]
        if len(merged) <= limits.hard_max:
            previous, tail = output[-2], output[-1]
            previous["text"] = merged
            previous["char_count"] = len(merged)
            previous["source_line_end"] = tail["source_line_end"]
            previous["clause_refs"] = clause_refs(merged)
            previous["reference_candidates"] = reference_candidates(merged)
            previous["quality_flags"] = ordered_unique(previous["quality_flags"] + tail["quality_flags"])
            output.pop()
    return output


def _row_parts(table: ParsedTable, row: list[str], limits: Limits) -> tuple[list[str], bool]:
    whole = serialize_row(table.headers, row)
    if len(whole) <= limits.hard_max:
        return [whole], False
    longest = max(range(len(row)), key=lambda i: len(row[i]))
    identity = list(row)
    identity[longest] = ""
    prefix = serialize_row(table.headers, identity)
    allowance = max(200, limits.target - len(prefix) - 2)
    hard_allowance = max(allowance, limits.hard_max - len(prefix) - 2)
    content_parts, forced = natural_split(row[longest], allowance, hard_allowance)
    rendered = []
    for part in content_parts:
        cells = list(row)
        cells[longest] = part
        rendered.append(serialize_row(table.headers, cells))
    return rendered, forced


def build_table(event: Event, limits: Limits) -> list[dict]:
    table = parse_html_table(event.raw_text)
    special = "AMENDMENT_TABLE" if any(word in (event.chapter_heading or "") + (event.section_heading or "")
                                        for word in AMENDMENT_WORDS) else None
    row_units = []
    if table.headers and not table.rows:
        text = "表头：" + " | ".join(table.headers)
        chunk = _base_chunk(event, "TABLE", text)
        chunk.update(table_index=event.table_index, table_row_start=0,
                     table_row_end=0, table_headers=table.headers, special_type=special)
        return [chunk]
    for row_index, row in enumerate(table.rows, 1):
        parts, forced = _row_parts(table, row, limits)
        for part_index, text in enumerate(parts, 1):
            row_units.append({"row": row_index, "text": text, "part": part_index,
                              "total": len(parts), "forced": forced})
    output, current = [], []

    def close():
        nonlocal current
        if not current:
            return
        text = "\n\n".join(unit["text"] for unit in current)
        chunk = _base_chunk(event, "TABLE", text)
        chunk.update(table_index=event.table_index,
                     table_row_start=current[0]["row"], table_row_end=current[-1]["row"],
                     table_headers=table.headers, special_type=special)
        if len(current) == 1 and current[0]["total"] > 1:
            chunk["table_row_part"] = {"part": current[0]["part"], "total": current[0]["total"]}
        if any(unit["forced"] for unit in current):
            chunk["quality_flags"].append("FORCED_TABLE_CELL_SPLIT")
        output.append(chunk)
        current = []

    for unit in row_units:
        candidate = "\n\n".join(x["text"] for x in current + [unit])
        if current and (unit["total"] > 1 or current[0]["total"] > 1 or len(candidate) > limits.soft_max):
            close()
        current.append(unit)
        if unit["total"] > 1:
            close()
    close()
    return output


def build_chunks(events: list[Event], limits: Limits) -> tuple[list[dict], dict[int, ParsedTable]]:
    limits.validate()
    chunks: list[dict] = []
    parsed_tables: dict[int, ParsedTable] = {}
    region: list[Event] = []

    def close_region():
        nonlocal region
        chunks.extend(build_text_region(region, limits))
        region = []

    for event in events:
        if event.event_type == "TEXT":
            region.append(event)
        elif event.event_type == "HEADING":
            close_region()
        elif event.event_type == "TABLE":
            close_region()
            parsed_tables[event.table_index] = parse_html_table(event.raw_text)
            chunks.extend(build_table(event, limits))
    close_region()
    return chunks, parsed_tables
