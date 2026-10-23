from __future__ import annotations

from rank_bm25 import BM25Okapi


def bm25_rankings(chunks: list[dict], retrieval_texts: dict[str, str], tokenizer, config: dict) -> list[dict]:
    corpus = [tokenizer.tokenize(retrieval_texts[chunk["chunk_id"]]) for chunk in chunks]
    model = BM25Okapi(corpus)
    top_n = config.get("bm25_top_n_per_query", 50)
    output = []
    for query in config["queries"]:
        scores = model.get_scores(tokenizer.tokenize(query["text"]))
        ranked = sorted(range(len(chunks)), key=lambda i: (-float(scores[i]), chunks[i]["source_order"]))
        positive = [i for i in ranked if float(scores[i]) > 0][:top_n]
        for rank, index in enumerate(positive, 1):
            output.append({"query_id": query["id"], "query_text": query["text"],
                           "query_channel": query.get("channel", "risk"),
                           "query_weight": float(query.get("weight", 1.0)),
                           "rule_ids": query.get("rule_ids", []),
                           "boundary_ids": query.get("boundary_ids", []),
                           "chunk_id": chunks[index]["chunk_id"], "rank": rank,
                           "bm25_rank": rank,
                           "bm25_score": round(float(scores[index]), 8)})
    return output
