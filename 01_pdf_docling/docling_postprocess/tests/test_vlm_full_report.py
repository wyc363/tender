import unittest

from docling_postprocess.vlm_full_report import build_statistics, find_over_heading


class VLMFullReportTests(unittest.TestCase):
    def test_over_heading_is_audit_only_and_statistics_keep_model_result(self):
        page = {"page_no": 1, "blocks": [{"block_id": "P0001-B001", "kind": "text",
            "parser_label": "list_item", "text": "2.1.项目名称：这是包含具体内容的完整条款；",
            "flags": []}]}
        row = {"page_no": 1, "status": "SUCCESS", "reading_order": ["P0001-B001"],
               "classifications": [{"block_id": "P0001-B001", "type": "heading",
                                    "heading_level": 2}],
               "confidence": .95, "structured_output_mode": "json_schema_strict",
               "compatibility_fallbacks": []}
        suspects = find_over_heading(page, row)
        stats, all_suspects = build_statistics([page], [row], 1, [1])
        self.assertEqual(suspects[0]["block_id"], "P0001-B001")
        self.assertEqual(stats["heading_blocks"], 1)
        self.assertEqual(len(all_suspects), 1)

    def test_invalid_page_is_not_counted_as_order_or_type_change(self):
        page = {"page_no": 1, "blocks": [{"block_id": "P0001-B001", "kind": "text",
            "parser_label": "section_header", "text": "标题", "flags": []}]}
        row = {"page_no": 1, "status": "INVALID", "reading_order": [],
               "classifications": [], "confidence": None,
               "structured_output_mode": "json_schema_strict",
               "compatibility_fallbacks": []}
        stats, suspects = build_statistics([page], [row], 1, [1])
        self.assertEqual(stats["pages_with_reading_order_change"], 0)
        self.assertEqual(stats["pages_with_type_change_vs_docling"], 0)
        self.assertEqual(stats["pages_not_comparable_due_to_invalid_or_failure"], 1)
        self.assertEqual(suspects, [])


if __name__ == "__main__":
    unittest.main()
