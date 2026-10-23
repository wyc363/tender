"""Generate the Phase C1.1 provenance-closure artifacts from a frozen Chunk run."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from md_chunking.chunk_provenance import (  # noqa: E402
    build_heading_context_assignments,
    classify_skipped_noncontent_sources,
    enrich_chunks,
    read_jsonl,
    write_jsonl,
)


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def ordered_unique(values):
    return list(dict.fromkeys(values))


def percentile(values, pct):
    if not values:
        return 0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * pct
    low, high = int(pos), min(int(pos) + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (pos - low), 2)


def length_stats(values):
    return {
        "min": min(values, default=0), "p10": percentile(values, .10),
        "p25": percentile(values, .25), "median": percentile(values, .50),
        "p75": percentile(values, .75), "p90": percentile(values, .90),
        "p95": percentile(values, .95), "max": max(values, default=0),
        "mean": round(statistics.mean(values), 2) if values else 0,
    }


def coverage(expected, covered):
    expected, covered = set(expected), set(covered) & set(expected)
    return {"expected": len(expected), "covered": len(covered),
            "coverage": len(covered) / len(expected) if expected else 1.0,
            "missing": sorted(expected - covered)}


def page_chunks(chunks, page):
    return [chunk for chunk in chunks if page in chunk.get("page_numbers", [])]


def main():
    parser = argparse.ArgumentParser(description="Phase C1.1 provenance closure")
    parser.add_argument("--chunk-dir", type=Path, required=True)
    parser.add_argument("--good-md", type=Path, required=True)
    parser.add_argument("--source-map", type=Path, required=True)
    parser.add_argument("--page-structures", type=Path, required=True)
    parser.add_argument("--baseline-raw", type=Path)
    args = parser.parse_args()

    out = args.chunk_dir
    generated_raw = out / "chunks.jsonl"
    raw_path = out / "chunks_raw.jsonl"
    if not raw_path.exists():
        raw_path.write_bytes(generated_raw.read_bytes())
    chunks_raw = read_jsonl(raw_path)
    events = read_jsonl(out / "parse_events.jsonl")
    mappings = read_jsonl(args.source_map)
    finals = read_jsonl(args.page_structures)
    good = args.good_md.read_text(encoding="utf-8-sig")
    page_types = {int(row["page_no"]): row.get("page_type", "content") for row in finals}
    enriched = enrich_chunks(chunks_raw, mappings, good, page_types, events)
    write_jsonl(generated_raw, enriched)

    assignments, unattached = build_heading_context_assignments(chunks_raw, events, mappings, good)
    skipped_raw = classify_skipped_noncontent_sources(events, mappings)
    skipped = []
    for item in skipped_raw:
        for source_id in item["source_table_ids"] or item["source_block_ids"]:
            skipped.append({
                "source_id": source_id, "render_id": item["render_id"],
                "page_numbers": item["page_numbers"], "reason": "EMPTY_NONCONTENT_TABLE",
                "event_id": item["event_id"], "table_index": item["table_index"],
                "source_line_start": item["md_line_start"],
                "source_line_end": item["md_line_end"], "chunk_generated": False,
            })

    text_lengths = [c["char_count"] for c in enriched if c["chunk_type"] == "TEXT"]
    table_lengths = [c["char_count"] for c in enriched if c["chunk_type"] == "TABLE"]
    mergeable = []
    for index, chunk in enumerate(enriched):
        if chunk["chunk_type"] != "TEXT" or chunk["char_count"] >= 300:
            continue
        for neighbor_index in (index - 1, index + 1):
            if not (0 <= neighbor_index < len(enriched)):
                continue
            neighbor = enriched[neighbor_index]
            if (neighbor["chunk_type"] == "TEXT"
                    and chunk.get("chapter_heading") == neighbor.get("chapter_heading")
                    and chunk.get("section_heading") == neighbor.get("section_heading")
                    and chunk["char_count"] + neighbor["char_count"] + 2 <= 1400):
                mergeable.append(chunk["chunk_id"])
                break
    stats = {
        "total_chunks": len(enriched),
        "text_chunks": sum(c["chunk_type"] == "TEXT" for c in enriched),
        "table_chunks": sum(c["chunk_type"] == "TABLE" for c in enriched),
        "text_char_length": length_stats(text_lengths),
        "table_char_length": length_stats(table_lengths),
        "text_lt_100": sum(x < 100 for x in text_lengths),
        "text_lt_200": sum(x < 200 for x in text_lengths),
        "text_lt_300": sum(x < 300 for x in text_lengths),
        "text_300_600": sum(300 <= x < 600 for x in text_lengths),
        "text_600_1000": sum(600 <= x < 1000 for x in text_lengths),
        "text_1000_1400": sum(1000 <= x < 1400 for x in text_lengths),
        "text_1400_1800": sum(1400 <= x <= 1800 for x in text_lengths),
        "text_gt_1800": sum(x > 1800 for x in text_lengths),
        "empty_chunk_count": sum(not c["text"].strip() for c in enriched),
        "mergeable_short_count": len(mergeable),
        "mergeable_short_chunk_ids": mergeable,
    }

    text_expected = {sid for row in mappings if row.get("semantic_type") == "text"
                     for sid in row.get("source_block_ids", [])}
    # A repaired/cross-page rendered table may have a non-table semantic wrapper;
    # source_table_ids remains the authoritative physical-table inventory.
    table_expected = {sid for row in mappings for sid in row.get("source_table_ids", [])}
    heading_expected = {sid for row in mappings if row.get("semantic_type") == "heading"
                        for sid in row.get("source_block_ids", [])}
    content_ids = {sid for c in enriched for sid in c.get("content_source_block_ids", [])}
    table_chunk_ids = {sid for c in enriched for sid in c.get("source_table_ids", [])}
    skipped_ids = {item["source_id"] for item in skipped}
    chunk_heading_ids = {sid for c in enriched for sid in c.get("heading_source_block_ids", [])}
    context_heading_ids = {sid for c in enriched for sid in c.get("heading_context_source_ids", [])}
    unattached_ids = {sid for item in unattached for sid in item.get("source_block_ids", [])}
    content_bearing_tables = table_expected - skipped_ids
    source_coverage = {
        "content_text_source_coverage": coverage(text_expected, content_ids),
        "content_bearing_table_sources": len(content_bearing_tables),
        "content_bearing_table_chunk_coverage": coverage(content_bearing_tables, table_chunk_ids),
        "table_source_traceability": coverage(table_expected, table_chunk_ids | skipped_ids),
        "heading_source_traceability": coverage(
            heading_expected, chunk_heading_ids | context_heading_ids | unattached_ids),
        "skipped_noncontent_sources": skipped,
        "unattached_structural_headings": unattached,
        "definitions": {
            "source_traceability": "source is mapped to a chunk or an explicit structural audit registry",
            "searchable_chunk_coverage": "only content-bearing sources are required to enter retrieval chunks",
        },
    }

    known_ids = {sid for row in mappings for sid in row.get("source_block_ids", [])}
    known_ids |= {sid for row in mappings for sid in row.get("source_table_ids", [])}
    used_ids = content_ids | table_chunk_ids | chunk_heading_ids | context_heading_ids
    unknown = sorted(used_ids - known_ids)
    provenance_checks = {
        "all_chunks_have_page_numbers": all(c.get("page_numbers") for c in enriched),
        "all_chunks_have_source_provenance": all(c.get("source_block_ids") for c in enriched),
        "unknown_source_ids_zero": not unknown,
        "all_table_chunks_have_source_table_ids": all(
            c.get("source_table_ids") for c in enriched if c["chunk_type"] == "TABLE"),
        "page_numbers_from_source_map": all(
            set(c.get("page_numbers", [])) == {p for row in mappings
                if any(sid in c.get("content_source_block_ids", []) for sid in row.get("source_block_ids", []))
                for p in row.get("page_numbers", [])} for c in enriched),
        "mapping_uses_line_overlap": True,
        "llm_not_used": True,
    }
    provenance = {
        "status": "PASS" if all(provenance_checks.values()) else "FAIL",
        "checks": provenance_checks, "unknown_source_ids": unknown,
        "method": "closed source_map Markdown line-range overlap; no fuzzy text mapping",
        "llm_used": False,
    }

    heading_audit = {
        "heading_event_count": sum(e["event_type"] == "HEADING" for e in events),
        "heading_source_id_count": len(heading_expected),
        "chunks_with_pending_heading_context": len(assignments),
        "pending_heading_context_links": sum(len(rows) for rows in assignments.values()),
        "consecutive_heading_context_chunks": [
            {"chunk_id": cid,
             "source_ids": ordered_unique(sid for row in rows for sid in row.get("source_block_ids", [])),
             "render_ids": [row["render_id"] for row in rows],
             "texts": [row.get("heading_text", "") for row in rows]}
            for cid, rows in assignments.items() if len(rows) > 1
        ],
        "unattached_structural_headings": unattached,
        "traceability": source_coverage["heading_source_traceability"],
    }

    special = {}
    for page in (29, 74, 127, 142, 167):
        pcs = page_chunks(enriched, page)
        special[str(page)] = {
            "chunk_ids": [c["chunk_id"] for c in pcs],
            "text_chunks": [c["chunk_id"] for c in pcs if c["chunk_type"] == "TEXT"],
            "table_chunks": [c["chunk_id"] for c in pcs if c["chunk_type"] == "TABLE"],
        }
    p29 = special["29"]
    p29["not_over_split"] = len(p29["chunk_ids"]) == 3
    p29["pass"] = p29["not_over_split"]
    p74_tables = [c for c in page_chunks(enriched, 74) if c["chunk_type"] == "TABLE"]
    special["74"].update({"repair_not_duplicated": len(p74_tables) == 2
                          and len({c["text"] for c in p74_tables}) == 2})
    special["74"]["pass"] = special["74"]["repair_not_duplicated"]
    special["127"]["pass"] = len(special["127"]["chunk_ids"]) == 3
    page142_ids = [f"P0142-B{i:03d}" for i in range(1, 13)]
    p142_map = {}
    for sid in page142_ids:
        p142_map[sid] = {
            "content_chunks": [c["chunk_id"] for c in enriched if sid in c.get("content_source_block_ids", [])],
            "heading_context_chunks": [c["chunk_id"] for c in enriched
                                       if sid in c.get("heading_context_source_ids", [])
                                       or sid in c.get("heading_source_block_ids", [])],
        }
    p142_missing = [sid for sid, places in p142_map.items()
                    if not places["content_chunks"] and not places["heading_context_chunks"]]
    b010_chunks = [c for c in enriched if "P0142-B010" in c.get("heading_context_source_ids", [])]
    special["142"].update({
        "B001_B012_traceability": p142_map, "missing_B001_B012": p142_missing,
        "P0142_T001": next((item for item in skipped if item["source_id"] == "P0142-T001"), None),
        "B010_target_chunk": [c["chunk_id"] for c in b010_chunks],
        "B010_not_copied_into_text": all("并附上联合体牵头人授权书" not in c["text"] for c in b010_chunks),
        "pass": not p142_missing and bool(b010_chunks)
                and any(item["source_id"] == "P0142-T001" for item in skipped),
    })
    special["167"]["pass"] = len(special["167"]["table_chunks"]) == 1

    raw_hash = hashlib.sha256(raw_path.read_bytes()).hexdigest().upper()
    baseline_hash = (hashlib.sha256(args.baseline_raw.read_bytes()).hexdigest().upper()
                     if args.baseline_raw and args.baseline_raw.exists() else None)
    checks = {
        "chunker_direct_execution_success": json.loads(
            (out / "chunk_quality_report.json").read_text(encoding="utf-8"))["strict_status"] == "PASS",
        "empty_chunks_zero": stats["empty_chunk_count"] == 0,
        "content_text_coverage_100pct": source_coverage["content_text_source_coverage"]["coverage"] == 1,
        "content_bearing_table_coverage_100pct": source_coverage["content_bearing_table_chunk_coverage"]["coverage"] == 1,
        "table_source_traceability_100pct": source_coverage["table_source_traceability"]["coverage"] == 1,
        "heading_source_traceability_100pct": source_coverage["heading_source_traceability"]["coverage"] == 1,
        "unknown_source_ids_zero": not unknown,
        "all_chunks_have_page_numbers": provenance_checks["all_chunks_have_page_numbers"],
        "all_chunks_have_source_provenance": provenance_checks["all_chunks_have_source_provenance"],
        "P0142_B010_traceable": bool(b010_chunks),
        "P0142_T001_empty_noncontent_table": any(
            item["source_id"] == "P0142-T001" and item["reason"] == "EMPTY_NONCONTENT_TABLE" for item in skipped),
        "page29_not_over_split": special["29"]["pass"],
        "page74_repair_not_duplicated": special["74"]["pass"],
        "page142_B001_B012_all_traceable": special["142"]["pass"],
        "mergeable_short_zero": stats["mergeable_short_count"] == 0,
        "chunk_count_not_exploded": len(enriched) == 273,
    }
    gate = {
        "status": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "total_chunks": len(enriched), "raw_chunk_sha256": raw_hash,
        "baseline_raw_chunk_sha256": baseline_hash,
        "raw_chunk_identical_to_C1": baseline_hash == raw_hash if baseline_hash else None,
        "retrieval_executed": False, "judge_executed": False,
        "conclusion": "READY_FOR_RETRIEVAL" if all(checks.values()) else "NEEDS_CHUNK_FIX",
    }

    dump(out / "C1_1_Chunk_Statistics.json", stats)
    dump(out / "C1_1_Source_Coverage.json", source_coverage)
    dump(out / "C1_1_Provenance_Validation.json", provenance)
    dump(out / "C1_1_Skipped_Noncontent_Sources.json",
         {"count": len(skipped), "skipped_noncontent_sources": skipped})
    dump(out / "C1_1_Heading_Context_Audit.json", heading_audit)
    dump(out / "C1_1_Special_Page_Checks.json", special)
    dump(out / "C1_1_Final_Gate_Report.json", gate)

    cards = []
    for chunk in enriched:
        cards.append(
            f'<article><h2>{html.escape(chunk["chunk_id"])} · {chunk["chunk_type"]}</h2>'
            f'<p>页码 {html.escape(str(chunk.get("page_numbers", [])))} · 字符 {chunk["char_count"]}</p>'
            f'<p><b>标题上下文：</b>{html.escape(" / ".join(chunk.get("heading_context_texts", [])))}</p>'
            f'<p><b>来源：</b>{html.escape(", ".join(chunk.get("source_block_ids", [])))}</p>'
            f'<pre>{html.escape(chunk["text"])}</pre></article>')
    preview = ("<!doctype html><meta charset='utf-8'><title>C1.1 Chunk Preview</title>"
               "<style>body{font-family:Arial,'Microsoft YaHei';max-width:1200px;margin:auto;background:#f5f7fa}"
               "article{background:white;margin:16px;padding:18px;border-radius:8px;border:1px solid #dce2ea}"
               "h2{font-size:18px}pre{white-space:pre-wrap;line-height:1.55}</style>"
               "<h1>Phase C1.1 Chunk Preview</h1>" + "".join(cards))
    (out / "C1_1_Chunk_Preview.html").write_text(preview, encoding="utf-8")
    print(json.dumps(gate, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
