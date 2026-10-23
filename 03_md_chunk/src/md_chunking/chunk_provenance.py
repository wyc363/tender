"""Deterministic source_map line-range enrichment for Markdown chunks."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from .table_parser import is_noncontent_empty_table


def ordered_unique(values: Iterable[Any]) -> list[Any]:
    return list(dict.fromkeys(values))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def overlaps(start: int, end: int, mapping: dict[str, Any]) -> bool:
    return int(mapping["md_line_start"]) <= end and int(mapping["md_line_end"]) >= start


def _heading_text(mapping: dict[str, Any], lines: list[str]) -> str:
    line = lines[int(mapping["md_line_start"]) - 1] if lines else ""
    return re.sub(r"^#{1,6}\s+", "", line).strip()


def _nearest_heading(headings: list[dict[str, Any]], text: str | None,
                     before_line: int) -> dict[str, Any] | None:
    if not text:
        return None
    matches = [item for item in headings
               if item["heading_text"] == text and int(item["md_line_start"]) < before_line]
    return max(matches, key=lambda item: int(item["md_line_start"]), default=None)


def _event_value(event: Any, name: str) -> Any:
    return event.get(name) if isinstance(event, dict) else getattr(event, name)


def _mapped_heading_rows(event: Any, ordered_map: list[dict[str, Any]],
                         lines: list[str]) -> list[dict[str, Any]]:
    start, end = int(_event_value(event, "line_start")), int(_event_value(event, "line_end"))
    return [{**row, "heading_text": _heading_text(row, lines)} for row in ordered_map
            if row.get("semantic_type") == "heading" and overlaps(start, end, row)]


def build_heading_context_assignments(
        chunks: list[dict[str, Any]], events: list[Any], source_map: list[dict[str, Any]],
        good_markdown: str) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Attach every pending heading to the next content-bearing chunk by line range."""
    lines = good_markdown.splitlines()
    ordered_map = sorted(source_map, key=lambda row: (int(row["md_line_start"]), row["render_id"]))
    ordered_chunks = sorted(chunks, key=lambda row: (
        int(row["source_line_start"]), int(row.get("source_order", 0))))
    assignments: dict[str, list[dict[str, Any]]] = {}
    pending: list[dict[str, Any]] = []

    for event in sorted(events, key=lambda item: int(_event_value(item, "line_start"))):
        event_type = _event_value(event, "event_type")
        if event_type == "HEADING":
            pending.extend(_mapped_heading_rows(event, ordered_map, lines))
            continue
        if event_type not in {"TEXT", "TABLE"}:
            continue
        if event_type == "TABLE" and is_noncontent_empty_table(_event_value(event, "raw_text")):
            continue
        start, end = int(_event_value(event, "line_start")), int(_event_value(event, "line_end"))
        candidates = [chunk for chunk in ordered_chunks
                      if chunk.get("chunk_type") == event_type
                      and int(chunk["source_line_start"]) <= end
                      and int(chunk["source_line_end"]) >= start]
        if pending and candidates:
            assignments.setdefault(candidates[0]["chunk_id"], []).extend(pending)
            pending = []

    unattached = []
    for row in pending:
        unattached.append({
            "classification": "UNATTACHED_STRUCTURAL_HEADING",
            "render_id": row["render_id"],
            "source_block_ids": row.get("source_block_ids", []),
            "page_numbers": row.get("page_numbers", []),
            "heading_text": row.get("heading_text", ""),
            "md_line_start": row["md_line_start"],
            "md_line_end": row["md_line_end"],
        })
    return assignments, unattached


def classify_skipped_noncontent_sources(events: list[Any], source_map: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Register line-mapped sources for empty non-content tables without making chunks."""
    ordered_map = sorted(source_map, key=lambda row: (int(row["md_line_start"]), row["render_id"]))
    skipped: list[dict[str, Any]] = []
    for event in events:
        if (_event_value(event, "event_type") != "TABLE"
                or not is_noncontent_empty_table(_event_value(event, "raw_text"))):
            continue
        start, end = int(_event_value(event, "line_start")), int(_event_value(event, "line_end"))
        for row in ordered_map:
            if row.get("semantic_type") == "table" and overlaps(start, end, row):
                skipped.append({
                    "classification": "EMPTY_NONCONTENT_TABLE",
                    "event_id": _event_value(event, "event_id"),
                    "table_index": _event_value(event, "table_index"),
                    "render_id": row["render_id"],
                    "source_block_ids": row.get("source_block_ids", []),
                    "source_table_ids": row.get("source_table_ids", []),
                    "page_numbers": row.get("page_numbers", []),
                    "md_line_start": row["md_line_start"],
                    "md_line_end": row["md_line_end"],
                    "chunk_generated": False,
                })
    return skipped


def enrich_chunks(chunks: list[dict[str, Any]], source_map: list[dict[str, Any]],
                  good_markdown: str, page_types: dict[int, str],
                  events: list[Any] | None = None) -> list[dict[str, Any]]:
    """Map Chunk ranges to source facts; no text search is used for content provenance."""
    lines = good_markdown.splitlines()
    ordered_map = sorted(source_map, key=lambda row: (int(row["md_line_start"]), row["render_id"]))
    headings = [{**row, "heading_text": _heading_text(row, lines)} for row in ordered_map
                if row.get("semantic_type") == "heading"]
    pending_assignments = (build_heading_context_assignments(
        chunks, events, source_map, good_markdown)[0] if events is not None else {})
    enriched: list[dict[str, Any]] = []
    for chunk in chunks:
        start, end = int(chunk["source_line_start"]), int(chunk["source_line_end"])
        content = [row for row in ordered_map
                   if row.get("semantic_type") != "heading" and overlaps(start, end, row)]
        pages = ordered_unique(page for row in content for page in row.get("page_numbers", []))
        source_ids = ordered_unique(source_id for row in content for source_id in row.get("source_block_ids", []))
        table_ids = ordered_unique(source_id for row in content for source_id in row.get("source_table_ids", []))
        render_ids = ordered_unique(row["render_id"] for row in content)
        context_rows = []
        for heading_text in ordered_unique([chunk.get("chapter_heading"), chunk.get("section_heading")]):
            found = _nearest_heading(headings, heading_text, start)
            if found:
                context_rows.append(found)
        heading_ids = ordered_unique(source_id for row in context_rows
                                     for source_id in row.get("source_block_ids", []))
        pending_rows = pending_assignments.get(chunk.get("chunk_id"), [])
        heading_context_source_ids = ordered_unique(
            source_id for item in pending_rows for source_id in item.get("source_block_ids", []))
        heading_context_render_ids = ordered_unique(item["render_id"] for item in pending_rows)
        heading_context_texts = ordered_unique(item.get("heading_text", "") for item in pending_rows)
        all_heading_rows = sorted([*context_rows, *pending_rows],
                                  key=lambda item: (int(item["md_line_start"]), item["render_id"]))
        all_heading_ids = ordered_unique(
            source_id for item in all_heading_rows for source_id in item.get("source_block_ids", []))
        regions = ordered_unique("toc" if page_types.get(int(page)) == "toc" else "body" for page in pages)
        region = regions[0] if len(regions) == 1 else ("mixed" if regions else None)
        row = dict(chunk)
        row.update({
            "heading_path": ordered_unique(x for x in [chunk.get("chapter_heading"), chunk.get("section_heading")] if x),
            "page_numbers": pages,
            "source_block_ids": source_ids,
            "content_source_block_ids": source_ids,
            "source_table_ids": table_ids,
            "render_ids": render_ids,
            "heading_source_block_ids": all_heading_ids,
            "heading_context_source_ids": heading_context_source_ids,
            "heading_context_render_ids": heading_context_render_ids,
            "heading_context_texts": heading_context_texts,
            "document_region": region,
            "provenance_method": "SOURCE_MAP_LINE_RANGE_INTERSECTION",
        })
        enriched.append(row)
    return enriched


def enrich_files(chunks_raw: Path, source_map_path: Path, good_md: Path,
                 final_page_structures: Path, output_path: Path,
                 events_path: Path | None = None) -> list[dict[str, Any]]:
    chunks = read_jsonl(chunks_raw)
    mappings = read_jsonl(source_map_path)
    finals = read_jsonl(final_page_structures)
    page_types = {int(page["page_no"]): page.get("page_type", "content") for page in finals}
    events = read_jsonl(events_path) if events_path else None
    enriched = enrich_chunks(chunks, mappings, good_md.read_text(encoding="utf-8-sig"), page_types, events)
    write_jsonl(output_path, enriched)
    return enriched
