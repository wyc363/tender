from __future__ import annotations

from collections import Counter

from .table_parser import is_noncontent_empty_table


def quality_report(document_id, events, chunks, parse_stats, input_sha256, limits):
    event_counts = Counter(event.event_type for event in events)
    source_starts = [chunk["source_line_start"] for chunk in chunks]
    inversion_count = sum(b < a for a, b in zip(source_starts, source_starts[1:]))
    heading_lines = {event.line_start for event in events if event.event_type == "HEADING"}
    cross_heading = sum(any(chunk["source_line_start"] <= line <= chunk["source_line_end"]
                            for line in heading_lines) for chunk in chunks)
    table_event_indices = {event.table_index for event in events if event.event_type == "TABLE"}
    noncontent_table_indices = {
        event.table_index for event in events
        if event.event_type == "TABLE" and is_noncontent_empty_table(event.raw_text)
    }
    table_chunk_indices = {chunk.get("table_index") for chunk in chunks if chunk["chunk_type"] == "TABLE"}
    unaccounted_table_indices = table_event_indices - table_chunk_indices - noncontent_table_indices
    report = {
        "document_id": document_id,
        "input_sha256": input_sha256,
        "input_lines": parse_stats["input_lines"],
        "heading_event_count": event_counts["HEADING"],
        "text_event_count": event_counts["TEXT"],
        "table_event_count": event_counts["TABLE"],
        "text_chunk_count": sum(c["chunk_type"] == "TEXT" for c in chunks),
        "table_chunk_count": sum(c["chunk_type"] == "TABLE" for c in chunks),
        "total_chunk_count": len(chunks),
        "unclosed_table_count": parse_stats["unclosed_table_count"],
        "nonblank_unassigned_line_count": len(parse_stats["nonblank_unassigned_lines"]),
        "nonblank_unassigned_lines": parse_stats["nonblank_unassigned_lines"],
        "chunk_source_order_inversion_count": inversion_count,
        "chunk_cross_heading_boundary_count": cross_heading,
        "text_chunk_contains_html_table_tag_count": sum("<table" in c["text"].lower() or "</table>" in c["text"].lower()
                                                        for c in chunks if c["chunk_type"] == "TEXT"),
        "table_event_without_chunk_count": len(unaccounted_table_indices),
        "skipped_noncontent_table_event_count": len(noncontent_table_indices),
        "skipped_noncontent_table_event_indices": sorted(noncontent_table_indices),
        "unaccounted_table_event_indices": sorted(unaccounted_table_indices),
        "empty_chunk_count": sum(not c["text"].strip() for c in chunks),
        "oversized_text_chunk_count": sum(c["char_count"] > limits.hard_max for c in chunks if c["chunk_type"] == "TEXT"),
        "forced_text_split_count": sum("FORCED_TEXT_SPLIT" in c["quality_flags"] for c in chunks),
        "forced_table_cell_split_count": sum("FORCED_TABLE_CELL_SPLIT" in c["quality_flags"] for c in chunks),
        "amendment_table_chunk_count": sum(c["special_type"] == "AMENDMENT_TABLE" for c in chunks),
        "max_text_chunk_chars": max((c["char_count"] for c in chunks if c["chunk_type"] == "TEXT"), default=0),
        "max_table_chunk_chars": max((c["char_count"] for c in chunks if c["chunk_type"] == "TABLE"), default=0),
    }
    gates = {
        "G1_SOURCE_ORDER": inversion_count == 0,
        "G2_ALL_NONBLANK_ASSIGNED": (report["nonblank_unassigned_line_count"] == 0
                                     and report["table_event_without_chunk_count"] == 0),
        "G3_NO_CROSS_HEADING": cross_heading == 0,
        "G4_TABLE_TEXT_ISOLATION": report["text_chunk_contains_html_table_tag_count"] == 0,
        "G5_NO_EMPTY_CHUNK": report["empty_chunk_count"] == 0,
        "G6_TABLES_CLOSED": report["unclosed_table_count"] == 0,
    }
    report["gates"] = gates
    report["strict_status"] = "PASS" if all(gates.values()) else "FAIL"
    return report
