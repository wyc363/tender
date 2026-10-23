from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .chunk_builder import Limits, build_chunks
from .markdown_events import parse_markdown
from .quality import quality_report


def _write_jsonl(path: Path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _preview(document_id: str, chunks: list[dict]) -> str:
    lines = [f"# {document_id} Chunk Preview", ""]
    for chunk in chunks:
        lines += [f"## {chunk['chunk_id'].split('-', 1)[1]} | {chunk['chunk_type']}", "",
                  f"- lines: {chunk['source_line_start']}-{chunk['source_line_end']}",
                  f"- chapter: {chunk['chapter_heading'] or ''}",
                  f"- section: {chunk['section_heading'] or ''}",
                  f"- clause_refs: {', '.join(chunk['clause_refs'])}",
                  f"- chars: {chunk['char_count']}"]
        if chunk["chunk_type"] == "TABLE":
            lines += [f"- table index: {chunk['table_index']}",
                      f"- row range: {chunk['table_row_start']}-{chunk['table_row_end']}",
                      f"- special_type: {chunk['special_type'] or ''}"]
            if "table_row_part" in chunk:
                value = chunk["table_row_part"]
                lines.append(f"- row part: {value['part']}/{value['total']}")
        if chunk["quality_flags"]:
            lines.append(f"- quality_flags: {', '.join(chunk['quality_flags'])}")
        lines += ["", chunk["text"], "", "---", ""]
    return "\n".join(lines)


def run_chunking(input_path, document_id, output_dir, limits=None, strict=False):
    source = Path(input_path)
    output = Path(output_dir)
    limits = limits or Limits()
    original = source.read_bytes()
    digest = hashlib.sha256(original).hexdigest().upper()
    markdown = original.decode("utf-8-sig")
    events, parse_stats = parse_markdown(markdown)
    chunks, parsed_tables = build_chunks(events, limits)
    for index, chunk in enumerate(chunks, 1):
        chunk["chunk_id"] = f"{document_id}-C{index:06d}"
        chunk["document_id"] = document_id
        chunk["source_file"] = source.name
        chunk["source_order"] = index
        ordered = {key: chunk[key] for key in ("chunk_id", "document_id", "source_file", "chunk_type", "source_order",
                   "source_line_start", "source_line_end", "chapter_heading", "section_heading", "text", "char_count",
                   "clause_refs", "reference_candidates", "special_type", "quality_flags")}
        ordered.update({key: value for key, value in chunk.items() if key not in ordered})
        chunk.clear(); chunk.update(ordered)
    report = quality_report(document_id, events, chunks, parse_stats, digest, limits)
    report["input_path"] = str(source.resolve())
    report["source_file_unchanged"] = hashlib.sha256(source.read_bytes()).hexdigest().upper() == digest
    report["table_business_row_counts"] = {str(i): len(table.rows) for i, table in parsed_tables.items()}
    output.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output / "parse_events.jsonl", (event.to_dict() for event in events))
    _write_jsonl(output / "chunks.jsonl", chunks)
    (output / "chunks_preview.md").write_text(_preview(document_id, chunks), encoding="utf-8")
    (output / "chunk_quality_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if strict and report["strict_status"] != "PASS":
        raise RuntimeError("Strict quality gates failed")
    return report, chunks, events, parsed_tables
