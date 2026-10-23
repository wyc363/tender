import json
import tempfile
import unittest
from pathlib import Path

from docling_postprocess.vlm_client import VLMClientResult
from docling_postprocess.vlm_config import ParserVLMSettings
from docling_postprocess.vlm_structure import run_structure_pages


def settings():
    return ParserVLMSettings("https://workspace.cn-beijing.example/v1", "secret", "qwen3.7-flash",
                             0, 1000, False, 120, 1, 1572864, 1)


class VLMStructureTests(unittest.TestCase):
    def fixture(self, root):
        image = root / "page.jpg"; image.write_bytes(b"jpeg")
        records = [
            {"page_no": 1, "blocks": [
                {"block_id": "P0001-B001", "kind": "text", "bbox_norm": [0, 0, 1, 1],
                 "parser_label": "text", "text": "正文", "flags": ["furniture_candidate"]},
                {"block_id": "P0001-B002", "kind": "text", "bbox_norm": [0, 0, 1, 1],
                 "parser_label": "text", "text": "重复", "flags": ["COVERED_BY_TABLE"]},
            ]},
            {"page_no": 2, "blocks": []},
        ]
        manifest = {"images": [{"page_no": 1, "path": str(image), "width_px": 400,
                                 "height_px": 600, "bytes": 4},
                                {"page_no": 2, "path": str(image), "width_px": 400,
                                 "height_px": 600, "bytes": 4}]}
        return records, manifest

    def test_success_usage_and_blank_page_skip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); records, manifest = self.fixture(root)
            def call(_settings, _image, prompt):
                self.assertNotIn("furniture_candidate", prompt)
                self.assertNotIn("P0001-B002", prompt)
                content = json.dumps({"page_no": 1, "reading_order": ["P0001-B001"],
                    "classifications": [{"block_id": "P0001-B001", "type": "paragraph",
                                         "heading_level": None}],
                    "page_type": "text", "confidence": 0.9})
                return VLMClientResult(content, {"id": "x"},
                    {"prompt_tokens": 20, "input_tokens": 20, "completion_tokens": 10,
                     "output_tokens": 10, "total_tokens": 30}, 0.2,
                    "json_schema_strict", True, [])
            rows, usage, check = run_structure_pages(records, manifest, [1, 2], root / "out",
                                                      settings(), call)
        self.assertEqual([row["status"] for row in rows], ["SUCCESS", "SKIP"])
        self.assertEqual(usage["pages_called"], 1)
        self.assertEqual(usage["total_tokens"], 30)
        self.assertEqual(usage["cost"]["status"], "estimate")
        self.assertEqual(check["status"], "PASS")

    def test_api_exception_saved_as_technical_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); records, manifest = self.fixture(root)
            def fail(*_args):
                raise RuntimeError("API unavailable")
            rows, usage, check = run_structure_pages(records, manifest, [1], root / "out",
                                                      settings(), fail)
            raw = json.loads((root / "out/raw_responses/page_0001.json").read_text("utf-8"))
        self.assertEqual(rows[0]["status"], "TECHNICAL_FAILED")
        self.assertEqual(usage["technical_failed"], 1)
        self.assertIn("API unavailable", raw["error"])
        self.assertEqual(check["status"], "COMPLETE_WITH_ISSUES")

    def test_resume_reuses_valid_success_without_duplicate_or_api_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); records, manifest = self.fixture(root); out = root / "out"
            content = json.dumps({"page_no": 1, "reading_order": ["P0001-B001"],
                "classifications": [{"block_id": "P0001-B001", "type": "paragraph",
                                     "heading_level": None}],
                "page_type": "text", "confidence": 0.9})
            def first_call(*_args):
                return VLMClientResult(content, {"id": "x"},
                    {"prompt_tokens": 20, "input_tokens": 20, "completion_tokens": 10,
                     "output_tokens": 10, "total_tokens": 30}, 0.2,
                    "json_schema_strict", True, [])
            run_structure_pages(records, manifest, [1], out, settings(), first_call)
            def forbidden(*_args):
                raise AssertionError("resume unexpectedly called API")
            rows, usage, _ = run_structure_pages(records, manifest, [1], out, settings(),
                                                  forbidden, resume=True)
            persisted = [json.loads(line) for line in
                         (out / "page_structures.jsonl").read_text("utf-8").splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(persisted), 1)
        self.assertEqual(usage["pages_reused_from_resume"], 1)
        self.assertEqual(usage["pages_called_this_run"], 0)
        self.assertEqual(usage["pages_called"], 1)


if __name__ == "__main__":
    unittest.main()
