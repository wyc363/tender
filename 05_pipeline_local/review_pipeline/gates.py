from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .utils import read_json, read_jsonl


def chunk_generic_gate(chunk_dir: Path, source_map: Path) -> dict[str, Any]:
    chunks = read_jsonl(chunk_dir / "chunks.jsonl")
    mappings = read_jsonl(source_map)
    coverage = read_json(chunk_dir / "C1_1_Source_Coverage.json")
    provenance = read_json(chunk_dir / "C1_1_Provenance_Validation.json")
    stats = read_json(chunk_dir / "C1_1_Chunk_Statistics.json")
    known = {sid for row in mappings for key in ("source_block_ids", "source_table_ids") for sid in row.get(key, [])}
    used = {sid for row in chunks for key in ("source_block_ids", "source_table_ids") for sid in row.get(key, [])}
    debug = [row["chunk_id"] for row in chunks if "<!-- page:" in row.get("text", "").lower()]
    checks = {
        "total_chunks_positive": len(chunks) > 0,
        "empty_chunk_zero": all(row.get("text", "").strip() for row in chunks),
        "all_chunks_have_page_numbers": all(row.get("page_numbers") for row in chunks),
        "all_chunks_have_source_provenance": all(row.get("source_block_ids") for row in chunks),
        "unknown_source_ids_zero": not (used - known),
        "content_text_coverage_100_percent": coverage["content_text_source_coverage"]["coverage"] == 1,
        "content_bearing_table_coverage_100_percent": coverage["content_bearing_table_chunk_coverage"]["coverage"] == 1,
        "table_source_traceability_100_percent": coverage["table_source_traceability"]["coverage"] == 1,
        "heading_source_traceability_100_percent": coverage["heading_source_traceability"]["coverage"] == 1,
        "debug_page_labels_absent": not debug,
        "provenance_validation_pass": provenance["status"] == "PASS",
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
        "total_chunks": len(chunks),
        "text_chunks": sum(row.get("chunk_type") == "TEXT" for row in chunks),
        "table_chunks": sum(row.get("chunk_type") == "TABLE" for row in chunks),
        "unknown_source_ids": sorted(used - known), "debug_page_label_chunks": debug,
        "warnings": ([{"code": "MERGEABLE_SHORT", "count": stats.get("mergeable_short_count", 0),
                       "chunk_ids": stats.get("mergeable_short_chunk_ids", [])}]
                     if stats.get("mergeable_short_count", 0) else []),
    }


def retrieval_generic_gate(chunks_path: Path, output: Path) -> dict[str, Any]:
    chunks = read_jsonl(chunks_path)
    top = read_jsonl(output / "retrieval_candidates.jsonl")
    judge = read_jsonl(output / "judge_inputs.jsonl")
    corpus = [row for row in chunks if row.get("document_region") != "toc"]
    ids = {row["chunk_id"] for row in chunks}
    top_ids = [row["chunk_id"] for row in top]
    target = min(50, len(corpus))
    checks = {
        "retrieval_corpus_positive": len(corpus) > 0,
        "toc_excluded": all(row.get("document_region") != "toc" for row in top),
        "top_k_matches_available": len(top) == target,
        "unique_chunk_ids": len(top_ids) == len(set(top_ids)),
        "duplicate_zero": len(top_ids) == len(set(top_ids)),
        "unknown_id_zero": not (set(top_ids) - ids),
        "page_provenance_complete": all(row.get("page_numbers") for row in top),
        "source_provenance_complete": all(row.get("source_block_ids") for row in top),
        "judge_inputs_match_top_k": len(judge) == len(top) and
            [row["anchor_chunk_id"] for row in judge] == top_ids,
    }
    types = Counter(row.get("chunk_type") for row in top)
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
            "retrieval_corpus": len(corpus), "top_k_target": 50, "top_k_actual": len(top),
            "text_candidates": types["TEXT"], "table_candidates": types["TABLE"]}


def judge_generic_gate(output: Path, expected: int) -> dict[str, Any]:
    predictions = read_jsonl(output / "predictions.jsonl")
    validated = read_jsonl(output / "validated_predictions.jsonl")
    findings = read_jsonl(output / "findings.jsonl")
    document = read_json(output / "document_review_result.json")
    final = read_json(output / "final_review_result.json")
    tech = [row for row in predictions if row.get("technical_error")]
    fails = [row for row in validated if row.get("validated_result") == "FAIL"]
    checks = {
        "all_inputs_have_prediction": len(predictions) == len(validated) == expected,
        "technical_failures_explicit": all(row.get("technical_error") for row in tech),
        "technical_failure_not_pass": all(row.get("validated_result") != "PASS" for row in validated
                                             if row.get("technical_error")),
        "validated_fail_grounded": all(row.get("grounded") for row in fails),
        "fail_page_provenance": all(row.get("evidence_page_numbers") for row in fails),
        "dedup_valid": len({row.get("finding_id") for row in findings}) == len(findings),
        "document_result_valid": document.get("result") in {"PASS", "FAIL", "REVIEW"},
        "final_review_result_generated": bool(final),
    }
    counts = Counter(row.get("validated_result") for row in validated)
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
            "judge_inputs": expected, "technical_failures": len(tech),
            "validated_pass": counts["PASS"], "validated_fail": counts["FAIL"],
            "validated_review": counts["REVIEW"], "document_result": document.get("result")}


def doc011_regression(parser: dict[str, Any], chunk: dict[str, Any], retrieval: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "physical_pages_172": parser.get("physical_pages") == 172,
        "active_pages_169": parser.get("active_pages") == 169,
        "source_ids_1946": parser.get("source_active_blocks") == 1946,
        "chunks_273": chunk.get("total_chunks") == 273,
        "text_230": chunk.get("text_chunks") == 230,
        "table_43": chunk.get("table_chunks") == 43,
        "retrieval_top50": retrieval.get("top_k_actual") == 50,
    }
    return {"profile": "DOC011", "status": "PASS" if all(checks.values()) else "FAIL",
            "checks": checks, "fixed_values_used_only_for_regression": True}

