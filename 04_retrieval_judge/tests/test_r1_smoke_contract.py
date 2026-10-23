import os
import subprocess
import sys
import unittest
from pathlib import Path

from retrieval.context import add_context_windows
from retrieval.fusion import fuse
from retrieval.pipeline import _retrieval_text
from retrieval.rule_store import compile_rule_store_config


class R1SmokeContractTests(unittest.TestCase):
    def _chunk(self, cid, order, text, pages, kind="TEXT", region="body"):
        return {
            "document_id": "D", "chunk_id": cid, "source_file": "good.md",
            "chunk_type": kind, "special_type": None, "chapter_heading": "第1章",
            "section_heading": "评分", "heading_path": ["第1章", "评分"],
            "heading_source_block_ids": [f"H{order}"],
            "heading_context_source_ids": [f"HC{order}"],
            "heading_context_texts": ["业绩要求"], "source_line_start": order,
            "source_line_end": order, "page_numbers": pages,
            "source_block_ids": [f"B{order}"], "source_table_ids": [],
            "render_ids": [f"R{order}"], "document_region": region,
            "text": text, "source_order": order, "char_count": len(text),
        }

    def test_retrieval_text_uses_heading_context(self):
        text = _retrieval_text(self._chunk("C1", 1, "正文", [1]))
        self.assertIn("业绩要求", text)

    def test_retrieval_text_deduplicates_heading(self):
        text = _retrieval_text(self._chunk("C1", 1, "正文", [1]))
        self.assertEqual(text.splitlines().count("第1章"), 1)

    def test_fusion_retains_page_and_provenance(self):
        chunk = self._chunk("C1", 1, "本省业绩得分", [8])
        bm25 = [{"query_id": "Q", "chunk_id": "C1", "rank": 1, "bm25_score": 2.0}]
        row = fuse([chunk], bm25, [], {"rrf_k": 60, "queries": [{"id": "Q"}],
                                      "scan_scope_terms": []})[0]
        self.assertEqual(row["page_numbers"], [8])
        self.assertEqual(row["source_block_ids"], ["B1"])

    def test_fusion_exposes_bm25_rank_and_score(self):
        chunk = self._chunk("C1", 1, "本省业绩得分", [8])
        bm25 = [{"query_id": "Q", "chunk_id": "C1", "rank": 2, "bm25_score": 1.5}]
        row = fuse([chunk], bm25, [], {"rrf_k": 60, "queries": [{"id": "Q"}],
                                      "scan_scope_terms": []})[0]
        self.assertEqual((row["bm25_rank"], row["bm25_score"]), (2, 1.5))

    def test_anchor_pages_are_not_replaced_by_context_pages(self):
        chunks = [self._chunk("C1", 1, "前", [1]), self._chunk("C2", 2, "锚点", [2]),
                  self._chunk("C3", 3, "后", [3])]
        candidate = {"chunk_id": "C2", "text": "锚点"}
        add_context_windows([candidate], chunks, 1)
        self.assertEqual(candidate["page_numbers"], [2])
        self.assertEqual(candidate["context_page_numbers"], [1, 2, 3])

    def test_context_explicitly_separates_anchor_before_after(self):
        chunks = [self._chunk("C1", 1, "前", [1]), self._chunk("C2", 2, "锚点", [2]),
                  self._chunk("C3", 3, "后", [3])]
        candidate = {"chunk_id": "C2", "text": "锚点"}
        add_context_windows([candidate], chunks, 1)
        self.assertEqual(candidate["anchor_chunk"]["chunk_id"], "C2")
        self.assertEqual(candidate["context_before"][0]["chunk_id"], "C1")
        self.assertEqual(candidate["context_after"][0]["chunk_id"], "C3")

    def test_table_anchor_can_use_neighbors(self):
        chunks = [self._chunk("C1", 1, "前", [1]),
                  self._chunk("C2", 2, "表", [2], kind="TABLE"),
                  self._chunk("C3", 3, "后", [3])]
        candidate = {"chunk_id": "C2", "text": "表"}
        add_context_windows([candidate], chunks, 1, table_neighbors=1)
        self.assertEqual(candidate["context_chunk_ids"], ["C1", "C2", "C3"])

    def test_cli_imports_without_pythonpath(self):
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        result = subprocess.run([sys.executable, str(root / "run_candidate_retrieval.py"), "--help"],
                                cwd=root, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
