import json
import unittest

from docling_postprocess.vlm_schema import parse_json_response, validate_page_structure


class VLMSchemaTests(unittest.TestCase):
    def setUp(self):
        self.active = [
            {"block_id": "P0001-B001", "kind": "text"},
            {"block_id": "P0001-T001", "kind": "table"},
        ]

    def valid(self):
        return {"page_no": 1, "reading_order": ["P0001-B001", "P0001-T001"],
                "classifications": [
                    {"block_id": "P0001-B001", "type": "heading", "heading_level": 2},
                    {"block_id": "P0001-T001", "type": "table", "heading_level": None},
                ], "page_type": "mixed", "confidence": 0.95}

    def test_normal_structure_json(self):
        value, errors = parse_json_response(json.dumps(self.valid()))
        self.assertFalse(errors)
        self.assertEqual(validate_page_structure(value, 1, self.active), [])

    def test_unknown_block_id(self):
        value = self.valid(); value["reading_order"][0] = "NOPE"
        self.assertTrue(any("UNKNOWN" in item for item in
                            validate_page_structure(value, 1, self.active)))

    def test_reading_order_duplicate(self):
        value = self.valid(); value["reading_order"] = ["P0001-B001", "P0001-B001"]
        self.assertIn("READING_ORDER_DUPLICATE", validate_page_structure(value, 1, self.active))

    def test_active_block_missing(self):
        value = self.valid(); value["reading_order"].pop(); value["classifications"].pop()
        errors = validate_page_structure(value, 1, self.active)
        self.assertTrue(any("MISSING" in item for item in errors))

    def test_covered_block_returned(self):
        value = self.valid(); value["reading_order"].append("P0001-B999")
        value["classifications"].append(
            {"block_id": "P0001-B999", "type": "paragraph", "heading_level": None})
        errors = validate_page_structure(value, 1, self.active, {"P0001-B999"})
        self.assertTrue(any("COVERED_BY_TABLE" in item for item in errors))

    def test_table_cannot_become_paragraph(self):
        value = self.valid(); value["classifications"][1]["type"] = "paragraph"
        self.assertIn("TABLE_TYPE_CHANGED: P0001-T001",
                      validate_page_structure(value, 1, self.active))

    def test_invalid_heading_level(self):
        value = self.valid(); value["classifications"][0]["heading_level"] = 3
        self.assertIn("HEADING_LEVEL_INVALID: P0001-B001",
                      validate_page_structure(value, 1, self.active))

    def test_invalid_json(self):
        value, errors = parse_json_response("```json\n{}\n```")
        self.assertIsNone(value)
        self.assertTrue(errors[0].startswith("INVALID_JSON"))


if __name__ == "__main__":
    unittest.main()

