import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docling_postprocess.loader import Document
from docling_postprocess.page_blocks import build_page_records, write_page_blocks


def prov(page, left, top, right, bottom):
    return {"page_no": page, "bbox": {"l": left, "t": top, "r": right,
            "b": bottom, "coord_origin": "TOPLEFT"}}


class PageBlockTests(unittest.TestCase):
    def fixture(self):
        table = {
            "self_ref": "#/tables/0", "parent": {"$ref": "#/body"},
            "label": "table", "content_layer": "body",
            "prov": [prov(1, 10, 100, 190, 190)],
            "data": {"num_rows": 1, "num_cols": 1, "table_cells": [{
                "start_row_offset_idx": 0, "start_col_offset_idx": 0,
                "row_span": 1, "col_span": 1, "text": "表格里的原文"
            }]},
        }
        texts = [
            {"self_ref": "#/texts/0", "parent": {"$ref": "#/body"},
             "label": "page_header", "content_layer": "furniture", "text": "页眉",
             "prov": [prov(1, 10, 5, 190, 15)]},
            {"self_ref": "#/texts/1", "parent": {"$ref": "#/body"},
             "label": "section_header", "content_layer": "body", "text": "标题",
             "prov": [prov(1, 10, 30, 190, 50)]},
            {"self_ref": "#/texts/2", "parent": {"$ref": "#/body"},
             "label": "text", "content_layer": "body", "text": "表格里的原文",
             "prov": [prov(1, 20, 120, 180, 140)]},
        ]
        return {"name": "测试", "pages": {"1": {"size": {"width": 200, "height": 200}}},
                "texts": texts, "tables": [table], "pictures": [], "groups": [],
                "body": {"children": [{"$ref": item["self_ref"]} for item in texts]}}

    def test_furniture_exclusion_and_table_covered_audit(self):
        records, report = build_page_records(Document(self.fixture()))
        self.assertEqual(len(records), 1)
        self.assertEqual([block["kind"] for block in records[0]["blocks"]],
                         ["text", "table", "text"])
        covered = next(block for block in records[0]["blocks"]
                       if block["source_ref"] == "#/texts/2")
        self.assertIn("COVERED_BY_TABLE", covered["flags"])
        self.assertNotIn("#/texts/0", {block["source_ref"] for block in records[0]["blocks"]})
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(report["text_entries_in_blocks"], 2)
        self.assertEqual(report["furniture_text_entries_excluded"], 1)

    def test_jsonl_one_record_per_page(self):
        records, _ = build_page_records(Document(self.fixture()))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "page_blocks.jsonl"
            write_page_blocks(path, records)
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["page_no"] for row in rows], [1])
        ids = [block["block_id"] for block in rows[0]["blocks"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_build_is_offline(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("network called")):
            records, report = build_page_records(Document(self.fixture()))
        self.assertTrue(records)
        self.assertTrue(all(report["checks"].values()))


if __name__ == "__main__":
    unittest.main()

