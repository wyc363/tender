import unittest

from retrieval.keyword_retriever import keyword_ranking, score_keywords


CONFIG = {
    "risk_terms": ["业绩", "奖项"], "condition_terms": ["加分"],
    "region_industry_terms": ["本省"], "strong_terms": ["奖项"], "keyword_top_n": 100,
}


class KeywordTests(unittest.TestCase):
    def test_group_weights_and_bonuses(self):
        score, hits, _ = score_keywords("本省业绩奖项可以加分", CONFIG)
        self.assertEqual(score, 11)
        self.assertEqual(hits, ["业绩", "奖项", "加分", "本省"])

    def test_single_risk_term_is_recalled(self):
        chunks = [{"chunk_id": "C2", "source_order": 2}, {"chunk_id": "C1", "source_order": 1}]
        rows = keyword_ranking(chunks, {"C1": "获奖情况", "C2": "只有业绩"}, CONFIG)
        self.assertEqual([row["chunk_id"] for row in rows], ["C2"])
