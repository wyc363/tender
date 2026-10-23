import unittest

from retrieval.bm25_retriever import bm25_rankings
from retrieval.tokenizer import ChineseTokenizer


class BM25Tests(unittest.TestCase):
    def test_query_ranking_is_independent_and_positive(self):
        chunks = [{"chunk_id": "C1", "source_order": 1}, {"chunk_id": "C2", "source_order": 2},
                  {"chunk_id": "C3", "source_order": 3}]
        texts = {"C1": "本省类似业绩加分", "C2": "奖项评分", "C3": "完全无关内容"}
        config = {"bm25_top_n_per_query": 2, "queries": [{"id": "Q1", "text": "业绩 加分"},
                                                           {"id": "Q2", "text": "奖项 评分"}]}
        rows = bm25_rankings(chunks, texts, ChineseTokenizer(["类似业绩"]), config)
        self.assertEqual({row["query_id"] for row in rows}, {"Q1", "Q2"})
        self.assertTrue(all(row["bm25_score"] > 0 for row in rows))
        self.assertEqual(next(row for row in rows if row["query_id"] == "Q1")["chunk_id"], "C1")
