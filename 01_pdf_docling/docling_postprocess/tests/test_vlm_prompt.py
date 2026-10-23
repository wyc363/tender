import unittest

from docling_postprocess.vlm_prompt import active_blocks, build_user_prompt


class VLMPromptTests(unittest.TestCase):
    def test_table_html_and_audit_flags_are_not_sent(self):
        record = {"page_no": 1, "blocks": [{
            "block_id": "P0001-T001", "kind": "table", "parser_label": "table",
            "bbox_norm": [0, 0, 1, 1], "text": "A" * 2000,
            "table_html": "<table>SECRET_HTML</table>", "flags": ["furniture_candidate"],
        }]}
        prompt = build_user_prompt(1, active_blocks(record))
        self.assertNotIn("SECRET_HTML", prompt)
        self.assertNotIn("furniture_candidate", prompt)
        self.assertIn('"table_preview_truncated":true', prompt)
        self.assertLess(len(prompt), 1800)


if __name__ == "__main__":
    unittest.main()
