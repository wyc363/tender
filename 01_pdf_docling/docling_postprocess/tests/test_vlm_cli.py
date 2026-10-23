import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

from docling_postprocess.vlm_cli import run_check_only


class VLMCLITests(unittest.TestCase):
    def test_check_only_makes_zero_api_calls_and_skips_empty_page_estimate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); pdf = root / "sample.pdf"; source = root / "document.json"
            document = pymupdf.open(); document.new_page(width=200, height=300)
            document.new_page(width=200, height=300); document.save(pdf); document.close()
            data = {"name": "sample", "pages": {
                "1": {"size": {"width": 200, "height": 300}},
                "2": {"size": {"width": 200, "height": 300}}},
                "texts": [{"self_ref": "#/texts/0", "parent": {"$ref": "#/body"},
                           "label": "text", "content_layer": "body", "text": "正文",
                           "prov": [{"page_no": 1, "bbox": {"l": 10, "t": 20, "r": 100,
                                    "b": 40, "coord_origin": "TOPLEFT"}}]}],
                "tables": [], "groups": [], "pictures": [],
                "body": {"children": [{"$ref": "#/texts/0"}]}}
            source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            with patch("docling_postprocess.vlm_structure.call_page_structure") as api:
                report = run_check_only(pdf, source, root / "out", image_width=400)
            api.assert_not_called()
        self.assertEqual(report["api_calls_executed"], 0)
        self.assertEqual(report["future_api_estimate"]["calls"], 1)


if __name__ == "__main__":
    unittest.main()
