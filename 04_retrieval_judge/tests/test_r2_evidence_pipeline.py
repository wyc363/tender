import unittest

from r2.evidence_mapper import aggregate_result, exact_source_substring, map_evidence, normalize_evidence
from run_r2_pipeline import deduplicate_findings, infer_raw_result, validate_prediction


def judge_input():
    def chunk(cid, order, text, page):
        return {"chunk_id": cid, "chunk_type": "TEXT", "source_order": order, "text": text,
                "page_numbers": [page], "source_block_ids": [f"B{order}"], "source_table_ids": [],
                "heading_path": ["评标办法"], "document_region": "body"}
    return {"candidate_id": "DOC011-RP-K0002", "rule_id": "RP", "review_item": "规则",
            "rule_leaf_ids": ["1201.0"], "page_numbers": [2], "source_block_ids": ["B2"],
            "anchor_chunk_id": "DOC011-C000002", "anchor_chunk": chunk("DOC011-C000002", 2, "省级奖项得2分", 2),
            "context_before": [chunk("DOC011-C000001", 1, "评分标准", 1)],
            "context_after": [chunk("DOC011-C000003", 3, "说明", 3)]}


class R2EvidencePipelineTests(unittest.TestCase):
    def test_exact_substring(self):
        self.assertEqual(exact_source_substring("奖项得2分", "省级奖项得2分"), "奖项得2分")

    def test_whitespace_tolerant_returns_source_text(self):
        self.assertEqual(exact_source_substring("省级奖项得2分", "省级奖项 得2分"), "省级奖项 得2分")

    def test_paraphrase_is_not_grounded(self):
        self.assertIsNone(exact_source_substring("省奖加两分", "省级奖项得2分"))

    def test_anchor_wins_over_context_duplicate(self):
        row = judge_input()
        row["context_before"][0]["text"] = "省级奖项得2分"
        mapped = map_evidence("省级奖项得2分", row)
        self.assertEqual(mapped["selected"]["chunk_id"], "DOC011-C000002")

    def test_page_comes_from_matched_context_chunk(self):
        row = judge_input()
        mapped = map_evidence("评分标准", row)
        self.assertEqual(mapped["selected"]["page_numbers"], [1])

    def test_valid_grounded_fail(self):
        prediction = {"candidate_id": "DOC011-RP-K0002", "审查结果": "违规",
                      "违规原文": ["省级奖项得2分"], "模型候选违规原文": ["省级奖项得2分"],
                      "判断理由": "作为评分项", "parse_status": "OK", "technical_error": None}
        result = validate_prediction(prediction, judge_input())
        self.assertEqual(result["validated_result"], "FAIL")
        self.assertEqual(result["evidence_page_numbers"], [2])

    def test_ungrounded_fail_becomes_review(self):
        prediction = {"candidate_id": "DOC011-RP-K0002", "审查结果": "结果无效",
                      "违规原文": [], "模型候选违规原文": ["省奖加两分"],
                      "判断理由": "违规", "parse_status": "EVIDENCE_NOT_GROUNDED", "technical_error": None}
        result = validate_prediction(prediction, judge_input())
        self.assertEqual(result["validated_result"], "REVIEW")
        self.assertIn("INVALID_UNGROUNDED_FAIL", result["validation_errors"])

    def test_pass_has_no_violation_text(self):
        prediction = {"candidate_id": "DOC011-RP-K0002", "审查结果": "不违规",
                      "违规原文": [], "模型候选违规原文": [], "判断理由": "合法",
                      "parse_status": "OK", "technical_error": None}
        result = validate_prediction(prediction, judge_input())
        self.assertEqual(result["validated_result"], "PASS")
        self.assertIsNone(result["violation_original_text"])

    def test_aggregation_priority(self):
        self.assertEqual(aggregate_result([{"validated_result": "PASS"}, {"validated_result": "REVIEW"}]), "REVIEW")
        self.assertEqual(aggregate_result([{"validated_result": "REVIEW"}, {"validated_result": "FAIL"}]), "FAIL")

    def test_normalization_for_dedup(self):
        self.assertEqual(normalize_evidence("省级 奖项"), normalize_evidence("省级奖项"))

    def test_dedup_by_shared_source(self):
        base = {"document_id": "D", "rule_id": "R", "rule_name": "规则", "validated_result": "FAIL",
                "violation_original_text": "省级奖项得2分", "review_original_text": None, "reason": "原因",
                "evidence_page_numbers": [2], "evidence_source_block_ids": ["B2"],
                "evidence_source_table_ids": [], "evidence_heading_path": ["评标"],
                "evidence_exact_in_anchor": True, "grounding_status": "GROUNDED", "evidence_chunk_id": "C2"}
        rows = [{**base, "prediction_id": "P1", "chunk_id": "C2", "retrieval_rank": 1},
                {**base, "prediction_id": "P2", "chunk_id": "C3", "retrieval_rank": 2}]
        findings, report = deduplicate_findings(rows)
        self.assertEqual(len(findings), 1)
        self.assertEqual(report["duplicates_removed"], 1)

    def test_infer_raw_fail_from_invalid_output(self):
        row = {"审查结果": "结果无效", "raw_output": '{"is_pass": false, "reason": "x"}'}
        self.assertEqual(infer_raw_result(row), "FAIL")


if __name__ == "__main__":
    unittest.main()
