from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import copy
from pathlib import Path

from .bm25_retriever import bm25_rankings
from .context import add_context_windows
from .fusion import fuse
from .keyword_retriever import keyword_ranking
from .quality import retrieval_quality
from .tokenizer import ChineseTokenizer
from .rule_store import compile_rule_store_config


def _load_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path, rows):
    Path(path).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _retrieval_text(chunk):
    values = [chunk.get("chapter_heading"), chunk.get("section_heading")]
    values.extend(chunk.get("heading_context_texts", []))
    values.extend(chunk.get("heading_path", []))
    values.append(chunk["text"])
    return "\n".join(dict.fromkeys(value for value in values if value))


def _code_sha256():
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).resolve().parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest().upper()


def _retrieve(chunks, config, tokenizer):
    texts = {chunk["chunk_id"]: _retrieval_text(chunk) for chunk in chunks}
    keyword = keyword_ranking(chunks, texts, config)
    bm25 = bm25_rankings(chunks, texts, tokenizer, config)
    pool = fuse(chunks, bm25, keyword, config)
    return keyword, bm25, pool


def _preview(document_id, config, top):
    lines = [f"# {document_id} Candidate Retrieval Top {len(top)}", "",
             f"Review item: {config['review_item']}", ""]
    for candidate in top:
        lines += [f"## Rank {candidate['rank']} | {candidate['chunk_id']}", "",
                  f"- channels: {', '.join(candidate['retrieval_channels'])}",
                  f"- RRF: {candidate['rrf_score']:.12f}",
                  f"- keyword_hits: {', '.join(candidate['keyword_hits'])}",
                  f"- matched_rule_ids: {', '.join(candidate.get('matched_rule_ids', []))}",
                  f"- matched_boundary_ids: {', '.join(candidate.get('matched_boundary_ids', []))}",
                  f"- scan_scope_match: {str(candidate['scan_scope_match']).lower()}",
                  f"- chapter: {candidate['chapter_heading'] or ''}",
                  f"- section: {candidate['section_heading'] or ''}",
                  f"- source lines: {candidate['source_line_start']}-{candidate['source_line_end']}", "",
                  candidate["text"], "", "---", ""]
    return "\n".join(lines)


def run_retrieval(chunks_path, config_path, output_dir, strict=False, *,
                  rule_store_path=None, review_item=None, prompt_program_path=None):
    chunks_path, config_path, output = Path(chunks_path), Path(config_path), Path(output_dir)
    input_chunks = _load_jsonl(chunks_path)
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    rule_manifest = None
    if rule_store_path:
        if not review_item:
            raise ValueError("--review-item is required with --rule-store")
        config, rule_manifest = compile_rule_store_config(rule_store_path, review_item, config)
    if len(input_chunks) != config.get("expected_chunk_count", len(input_chunks)):
        raise ValueError(f"Expected {config['expected_chunk_count']} chunks, got {len(input_chunks)}")
    if len({chunk["chunk_id"] for chunk in input_chunks}) != len(input_chunks):
        raise ValueError("Duplicate chunk_id in input")
    required = {
        "chunk_id", "chunk_type", "text", "source_order", "page_numbers",
        "source_block_ids", "document_region", "heading_path",
        "heading_source_block_ids", "heading_context_source_ids", "heading_context_texts",
    }
    missing_schema = {chunk["chunk_id"]: sorted(required - set(chunk)) for chunk in input_chunks
                      if required - set(chunk)}
    if missing_schema:
        raise ValueError(f"Chunk schema is incomplete: {missing_schema}")
    chunks = [chunk for chunk in input_chunks if chunk.get("document_region") != "toc"]
    if not chunks:
        raise ValueError("Retrieval corpus is empty after TOC exclusion")
    custom = config.get("tokenizer_custom_terms", [])
    tokenizer = ChineseTokenizer(custom)
    keyword, bm25, pool = _retrieve(chunks, config, tokenizer)
    repeat_keyword, repeat_bm25, repeat_pool = _retrieve(chunks, config, ChineseTokenizer(custom))
    top_k = min(config.get("top_k", 50), len(pool))
    top = pool[:top_k]
    for rank, candidate in enumerate(top, 1):
        candidate["candidate_id"] = f"{chunks[0]['document_id']}-{config['review_item_id']}-K{rank:04d}"
        candidate["review_item"] = config["review_item"]
        candidate["rule_id"] = config["review_item_id"]
        candidate["rule_name"] = config["review_item"]
        candidate["rule_leaf_ids"] = config.get("selected_rule_ids", [])
        candidate["rank"] = rank
    context_options = {
        "table_neighbors": config.get("table_context_neighbors"),
        "expand_max_anchor_chars": config.get("context_expand_max_anchor_chars"),
    }
    add_context_windows(top, chunks, int(config.get("context_neighbors", 1)), **context_options)
    repeat_ids = [candidate["chunk_id"] for candidate in repeat_pool[:top_k]]
    report = retrieval_quality(chunks, pool, top, keyword, bm25, repeat_ids)
    corpus_text = "\n".join(candidate["text"] for candidate in top)
    concepts = config.get("smoke_concepts", {})
    concept_counts = {name: sum(any(term in candidate["text"] for term in terms) for candidate in top)
                      for name, terms in concepts.items()}
    smoke_checks = {name + "_present": count > 0 for name, count in concept_counts.items()}
    hard_negative = config.get("hard_negative_smoke_phrase")
    if hard_negative:
        smoke_checks["hard_negative_template_present"] = hard_negative in corpus_text
    report.update(
        tokenizer="jieba", jieba_version=importlib.metadata.version("jieba"),
        rank_bm25_version=importlib.metadata.version("rank-bm25"),
        top50_text_count=sum(c["chunk_type"] == "TEXT" for c in top),
        top50_table_count=sum(c["chunk_type"] == "TABLE" for c in top),
        scan_scope_match_count=sum(c["scan_scope_match"] for c in top),
        top50_under_100_chars=sum(c["char_count"] < 100 for c in top),
        top50_under_300_chars=sum(c["char_count"] < 300 for c in top),
        performance_related_count=concept_counts.get("performance", 0),
        award_honor_related_count=concept_counts.get("award_or_honor", 0),
        scoring_related_count=concept_counts.get("scoring", 0),
        smoke_checks=smoke_checks)
    report["smoke_status"] = "PASS" if all(report["smoke_checks"].values()) else "RECALL_RISK"
    report["strict_status"] = "PASS" if report["strict_status"] == "PASS" and report["smoke_status"] == "PASS" else "FAIL"
    output.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output / "bm25_query_results.jsonl", bm25)
    _write_jsonl(output / "keyword_results.jsonl", keyword)
    _write_jsonl(output / "candidate_pool.jsonl", pool)
    _write_jsonl(output / "candidate_top50.jsonl", top)
    _write_jsonl(output / "retrieval_candidates.jsonl", top)
    _write_jsonl(output / "context_enriched_candidates.jsonl", top)
    audit_top_k = min(int(config.get("audit_top_k", 100)), len(pool))
    audit_top = copy.deepcopy(pool[:audit_top_k])
    for rank, candidate in enumerate(audit_top, 1):
        candidate["pool_rank"] = rank
    add_context_windows(audit_top, chunks, int(config.get("context_neighbors", 1)), **context_options)
    _write_jsonl(output / "candidate_top100_audit.jsonl", audit_top)
    (output / "candidate_top50_preview.md").write_text(_preview(chunks[0]["document_id"], config, top), encoding="utf-8")
    (output / "retrieval_quality_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "candidate_review_template.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["rank", "chunk_id", "retrieval_relevant", "candidate_category", "contains_actual_condition", "notes"])
        for candidate in top:
            writer.writerow([candidate["rank"], candidate["chunk_id"], "", "", "", ""])
    manifest = {
        "chunks_path": str(chunks_path.resolve()),
        "chunks_sha256": hashlib.sha256(chunks_path.read_bytes()).hexdigest().upper(),
        "config_path": str(config_path.resolve()),
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest().upper(),
        "retrieval_code_sha256": _code_sha256(),
        "input_chunk_count": len(input_chunks),
        "indexed_chunk_count": len(chunks),
        "toc_excluded_chunk_count": len(input_chunks) - len(chunks),
        "document_id": chunks[0]["document_id"],
        "original_chunk_text_sha256": hashlib.sha256("".join(c["text"] for c in input_chunks).encode()).hexdigest().upper(),
    }
    if rule_manifest:
        (output / "compiled_retrieval_config.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        (output / "rule_selection_manifest.json").write_text(
            json.dumps(rule_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest["rule_store"] = rule_manifest
        compiled_bytes = json.dumps(config, ensure_ascii=False, sort_keys=True).encode("utf-8")
        manifest["compiled_config_sha256"] = hashlib.sha256(compiled_bytes).hexdigest().upper()
        judge_inputs = [{
            "candidate_id": candidate["candidate_id"],
            "review_item": config["review_item"],
            "rule_id": config["review_item_id"],
            "rule_leaf_ids": config.get("selected_rule_ids", []),
            "rule_context": config.get("judge_rule_context", config["rule_context_v2"]),
            "document_text": candidate["document_text"],
            "anchor_chunk": candidate["anchor_chunk"],
            "context_before": candidate["context_before"],
            "context_after": candidate["context_after"],
            "anchor_chunk_id": candidate["anchor_chunk_id"],
            "context_chunk_ids": candidate["context_chunk_ids"],
            "source_file": candidate.get("source_file"),
            "context_source_line_start": candidate["context_source_line_start"],
            "context_source_line_end": candidate["context_source_line_end"],
            "page_numbers": candidate.get("page_numbers", []),
            "context_page_numbers": candidate.get("context_page_numbers", []),
            "source_block_ids": candidate.get("source_block_ids", []),
            "source_table_ids": candidate.get("source_table_ids", []),
            "matched_rule_ids": candidate["matched_rule_ids"],
            "matched_boundary_ids": candidate["matched_boundary_ids"],
        } for candidate in top]
        _write_jsonl(output / "judge_inputs.jsonl", judge_inputs)
    if prompt_program_path:
        prompt_path = Path(prompt_program_path)
        prompt_program = json.loads(prompt_path.read_text(encoding="utf-8-sig"))
        fields = prompt_program.get("decide", {}).get("signature", {}).get("fields", [])
        prefixes = {field.get("prefix") for field in fields}
        required = {"Review Item:", "Rule Context:", "Document Text:"}
        if not required.issubset(prefixes):
            raise ValueError("DSPy program is missing required retrieval-to-Judge input fields")
        manifest["prompt_program_path"] = str(prompt_path.resolve())
        manifest["prompt_program_sha256"] = hashlib.sha256(prompt_path.read_bytes()).hexdigest().upper()
    (output / "retrieval_input_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if strict and report["strict_status"] != "PASS":
        raise RuntimeError("Retrieval quality gates failed")
    return report, top
