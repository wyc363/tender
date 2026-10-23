import json
import os
import unittest
from pathlib import Path


class RetrievalSmokeTests(unittest.TestCase):
    """Optional regression against external DOC0181 artifacts.

    The repository intentionally does not contain tender-document outputs. Set
    TENDER_REVIEW_DOC0181_RETRIEVAL_ROOT and TENDER_REVIEW_DOC0181_CHUNKS to run
    this historical regression locally.
    """

    @classmethod
    def setUpClass(cls):
        root_value = os.environ.get("TENDER_REVIEW_DOC0181_RETRIEVAL_ROOT")
        chunks_value = os.environ.get("TENDER_REVIEW_DOC0181_CHUNKS")
        if not root_value or not chunks_value:
            raise unittest.SkipTest("external DOC0181 regression artifacts are not configured")
        cls.root = Path(root_value)
        cls.chunks_path = Path(chunks_value)
        required = [
            cls.root / "candidate_top50.jsonl",
            cls.root / "retrieval_quality_report.json",
            cls.chunks_path,
        ]
        if not all(path.is_file() for path in required):
            raise unittest.SkipTest("configured DOC0181 regression artifacts are incomplete")
        cls.top = [json.loads(line) for line in (cls.root / "candidate_top50.jsonl").read_text(encoding="utf-8").splitlines()]
        cls.report = json.loads((cls.root / "retrieval_quality_report.json").read_text(encoding="utf-8"))

    def test_top50_and_quality(self):
        self.assertEqual(len(self.top), 50)
        self.assertEqual(self.report["strict_status"], "PASS")
        self.assertEqual([row["rank"] for row in self.top], list(range(1, 51)))

    def test_required_concepts_and_hard_negative(self):
        text = "\n".join(row["text"] for row in self.top)
        self.assertIn("业绩", text)
        self.assertTrue(any(term in text for term in ("奖项", "获奖", "荣誉")))
        self.assertTrue(any(term in text for term in ("评分", "分值", "得分")))
        self.assertIn("设计单位技术实力、以往业绩、获奖情况、信誉", text)

    def test_candidate_text_matches_frozen_chunks(self):
        chunks = {row["chunk_id"]: row for row in map(json.loads, self.chunks_path.read_text(encoding="utf-8").splitlines())}
        self.assertTrue(all(row["text"] == chunks[row["chunk_id"]]["text"] for row in self.top))
