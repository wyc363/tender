from __future__ import annotations

from collections import defaultdict


def scan_scope_match(chunk: dict, terms: list[str]) -> bool:
    heading = "\n".join(filter(None, [chunk.get("chapter_heading"), chunk.get("section_heading")]))
    return any(term in heading for term in terms)


def fuse(chunks: list[dict], bm25_rows: list[dict], keyword_rows: list[dict], config: dict) -> list[dict]:
    by_id = {chunk["chunk_id"]: chunk for chunk in chunks}
    b_hits = defaultdict(list)
    contributions = defaultdict(float)
    channels = defaultdict(set)
    matched_rules = defaultdict(set)
    matched_boundaries = defaultdict(set)
    rrf_k = config.get("rrf_k", 60)
    for row in bm25_rows:
        b_hits[row["chunk_id"]].append({"query_id": row["query_id"], "rank": row["rank"],
                                         "score": row["bm25_score"],
                                         "query_channel": row.get("query_channel", "risk"),
                                         "query_weight": row.get("query_weight", 1.0)})
        contributions[row["chunk_id"]] += row.get("query_weight", 1.0) / (rrf_k + row["rank"])
        channels[row["chunk_id"]].add("BM25_" + row["query_id"])
        matched_rules[row["chunk_id"]].update(row.get("rule_ids", []))
        matched_boundaries[row["chunk_id"]].update(row.get("boundary_ids", []))
    keyword = {row["chunk_id"]: row for row in keyword_rows}
    for row in keyword_rows:
        contributions[row["chunk_id"]] += 1 / (rrf_k + row["keyword_rank"])
        channels[row["chunk_id"]].add("KEYWORD")
    candidates = []
    query_order = {query["id"]: i for i, query in enumerate(config["queries"])}
    for chunk_id in contributions:
        chunk = by_id[chunk_id]
        kw = keyword.get(chunk_id, {})
        candidate = {key: chunk.get(key) for key in (
            "document_id", "chunk_id", "source_file", "chunk_type", "special_type",
            "chapter_heading", "section_heading", "heading_path", "heading_source_block_ids",
            "heading_context_source_ids", "heading_context_texts", "source_line_start", "source_line_end",
            "page_numbers", "source_block_ids", "source_table_ids", "render_ids", "document_region", "text")}
        ordered_hits = sorted(b_hits[chunk_id], key=lambda hit: (
            hit["rank"], query_order.get(hit["query_id"], 999)))
        best_hit = ordered_hits[0] if ordered_hits else None
        candidate.update(
            retrieval_channels=sorted(channels[chunk_id], key=lambda value: (value == "KEYWORD", query_order.get(value.removeprefix("BM25_"), 999))),
            bm25_hits=sorted(b_hits[chunk_id], key=lambda hit: query_order[hit["query_id"]]),
            bm25_score=best_hit["score"] if best_hit else None,
            bm25_rank=best_hit["rank"] if best_hit else None,
            matched_rule_ids=sorted(matched_rules[chunk_id]),
            matched_boundary_ids=sorted(matched_boundaries[chunk_id]),
            keyword_score=kw.get("keyword_score", 0), keyword_rank=kw.get("keyword_rank"),
            keyword_hits=kw.get("keyword_hits", []), matched_keywords=kw.get("keyword_hits", []),
            rrf_score=round(contributions[chunk_id], 12),
            scan_scope_match=scan_scope_match(chunk, config["scan_scope_terms"]),
            source_order=chunk["source_order"], char_count=chunk["char_count"])
        candidates.append(candidate)
    candidates.sort(key=lambda c: (-c["rrf_score"], -int(c["scan_scope_match"]),
                                   -c["keyword_score"], c["source_order"]))
    return candidates
