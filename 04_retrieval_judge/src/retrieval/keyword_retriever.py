from __future__ import annotations

from .tokenizer import normalize


GROUP_WEIGHTS = {
    "risk_terms": 1,
    "condition_terms": 1,
    "region_industry_terms": 1,
    "strong_terms": 2,
}


def score_keywords(text: str, config: dict) -> tuple[int, list[str], dict[str, list[str]]]:
    normalized = normalize(text)
    hits_by_group = {}
    score = 0
    for group, weight in GROUP_WEIGHTS.items():
        hits = [term for term in config[group] if normalize(term) in normalized]
        hits = list(dict.fromkeys(hits))
        hits_by_group[group] = hits
        score += weight * len(hits)
    if hits_by_group["risk_terms"] and hits_by_group["condition_terms"]:
        score += 3
    if (hits_by_group["region_industry_terms"] and hits_by_group["risk_terms"]
            and hits_by_group["condition_terms"]):
        score += 2
    hits = list(dict.fromkeys(term for group in GROUP_WEIGHTS for term in hits_by_group[group]))
    return score, hits, hits_by_group


def keyword_ranking(chunks: list[dict], retrieval_texts: dict[str, str], config: dict) -> list[dict]:
    results = []
    for chunk in chunks:
        score, hits, hits_by_group = score_keywords(retrieval_texts[chunk["chunk_id"]], config)
        if score > 0:
            results.append({"chunk_id": chunk["chunk_id"], "source_order": chunk["source_order"],
                            "keyword_score": score, "keyword_hits": hits,
                            "keyword_hits_by_group": hits_by_group})
    results.sort(key=lambda row: (-row["keyword_score"], row["source_order"]))
    top_n = config.get("keyword_top_n", 100)
    results = results[:top_n]
    for rank, row in enumerate(results, 1):
        row["keyword_rank"] = rank
        row["rank"] = rank
        row["matched_keywords"] = row["keyword_hits"]
    return results
