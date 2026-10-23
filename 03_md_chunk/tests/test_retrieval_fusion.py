import unittest

from retrieval.fusion import fuse


class FusionTests(unittest.TestCase):
    def test_rrf_and_original_text(self):
        chunks = [{"document_id": "D", "chunk_id": "C1", "chunk_type": "TEXT", "special_type": None,
                   "chapter_heading": "评标办法", "section_heading": "评分", "source_line_start": 1,
                   "source_line_end": 1, "text": "原文", "source_order": 1, "char_count": 2},
                  {"document_id": "D", "chunk_id": "C2", "chunk_type": "TEXT", "special_type": None,
                   "chapter_heading": None, "section_heading": None, "source_line_start": 2,
                   "source_line_end": 2, "text": "其他", "source_order": 2, "char_count": 2}]
        bm25 = [{"query_id": "Q1", "chunk_id": "C1", "rank": 1, "bm25_score": 2.0},
                {"query_id": "Q1", "chunk_id": "C2", "rank": 2, "bm25_score": 1.0}]
        keyword = [{"chunk_id": "C1", "keyword_rank": 1, "keyword_score": 3, "keyword_hits": ["评分"]}]
        config = {"rrf_k": 60, "queries": [{"id": "Q1"}], "scan_scope_terms": ["评标"]}
        result = fuse(chunks, bm25, keyword, config)
        self.assertEqual(result[0]["chunk_id"], "C1")
        self.assertEqual(result[0]["text"], "原文")
        self.assertEqual(result[0]["retrieval_channels"], ["BM25_Q1", "KEYWORD"])
        self.assertTrue(result[0]["scan_scope_match"])
