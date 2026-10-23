from __future__ import annotations


def retrieval_quality(chunks, candidates, top, keyword_rows, bm25_rows, repeat_top_ids):
    by_id = {chunk["chunk_id"]: chunk for chunk in chunks}
    ids = [candidate["chunk_id"] for candidate in top]
    pool_ids = [candidate["chunk_id"] for candidate in candidates]
    keyword_ids = {row["chunk_id"] for row in keyword_rows}
    bm25_ids = {row["chunk_id"] for row in bm25_rows}
    risk_bm25_ids = {row["chunk_id"] for row in bm25_rows if row.get("query_channel", "risk") == "risk"}
    boundary_bm25_ids = {row["chunk_id"] for row in bm25_rows if row.get("query_channel") == "boundary"}
    expected_ranks = list(range(1, len(top) + 1))
    checks = {
        "R_G1_ALL_CHUNKS_INDEXED": len(by_id) == len(chunks),
        "R_G2_NO_UNKNOWN_CANDIDATE": all(candidate["chunk_id"] in by_id for candidate in candidates),
        "R_G3_CANDIDATE_TEXT_UNCHANGED": all(candidate["text"] == by_id[candidate["chunk_id"]]["text"] for candidate in candidates),
        "R_G4_NO_DUPLICATE_CANDIDATE": len(pool_ids) == len(set(pool_ids)),
        "R_G5_CONTINUOUS_RANK": [candidate["rank"] for candidate in top] == expected_ranks,
        "R_G6_BOTH_CHANNELS_NONEMPTY": bool(keyword_ids) and bool(bm25_ids),
        "R_G7_REPRODUCIBLE_TOPK": ids == repeat_top_ids,
    }
    return {
        "indexed_chunk_count": len(chunks),
        "candidate_unknown_chunk_count": sum(candidate["chunk_id"] not in by_id for candidate in candidates),
        "candidate_text_mismatch_count": sum(candidate["text"] != by_id[candidate["chunk_id"]]["text"] for candidate in candidates),
        "duplicate_candidate_chunk_count": len(pool_ids) - len(set(pool_ids)),
        "keyword_candidate_count": len(keyword_ids),
        "bm25_unique_candidate_count": len(bm25_ids),
        "bm25_risk_unique_candidate_count": len(risk_bm25_ids),
        "bm25_boundary_unique_candidate_count": len(boundary_bm25_ids),
        "intersection_count": len(keyword_ids & bm25_ids),
        "union_count": len(keyword_ids | bm25_ids),
        "top_k_count": len(top),
        "checks": checks,
        "strict_status": "PASS" if all(checks.values()) else "FAIL",
    }
