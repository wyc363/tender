from __future__ import annotations

import re
import unicodedata


def compact_with_positions(text: str) -> tuple[str, list[int]]:
    compact, positions = [], []
    for index, char in enumerate(text):
        if not char.isspace():
            compact.append(char)
            positions.append(index)
    return "".join(compact), positions


def exact_source_substring(proposed: str, source: str) -> str | None:
    cleaned = proposed.strip().strip('"“”\'‘’')
    if not cleaned:
        return None
    if cleaned in source:
        return cleaned
    source_compact, positions = compact_with_positions(source)
    proposed_compact, _ = compact_with_positions(cleaned)
    start = source_compact.find(proposed_compact)
    if start < 0:
        return None
    end = start + len(proposed_compact) - 1
    return source[positions[start]:positions[end] + 1]


def candidate_source_chunks(judge_input: dict) -> list[dict]:
    return [judge_input["anchor_chunk"], *judge_input.get("context_before", []),
            *judge_input.get("context_after", [])]


def map_evidence(proposed: str, judge_input: dict) -> dict:
    anchor = judge_input["anchor_chunk"]
    matches = []
    for chunk in candidate_source_chunks(judge_input):
        exact = exact_source_substring(proposed, chunk["text"])
        if exact is None:
            continue
        matches.append({
            "chunk_id": chunk["chunk_id"], "exact_text": exact,
            "is_anchor": chunk["chunk_id"] == anchor["chunk_id"],
            "source_order": chunk["source_order"], "page_numbers": chunk.get("page_numbers", []),
            "source_block_ids": chunk.get("source_block_ids", []),
            "source_table_ids": chunk.get("source_table_ids", []),
            "heading_path": chunk.get("heading_path", []),
        })
    matches.sort(key=lambda item: (
        not item["is_anchor"], abs(item["source_order"] - anchor["source_order"]), item["source_order"]))
    return {
        "grounded": bool(matches), "selected": matches[0] if matches else None,
        "evidence_match_candidates": matches,
    }


def normalize_evidence(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).lower()


def aggregate_result(validated_predictions: list[dict]) -> str:
    if any(row["validated_result"] == "FAIL" for row in validated_predictions):
        return "FAIL"
    if any(row["validated_result"] == "REVIEW" for row in validated_predictions):
        return "REVIEW"
    return "PASS"
