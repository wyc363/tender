import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docling_postprocess.deterministic_structure import (
    build_final_page,
    detect_structural_enumeration_runs,
    source_text_coverage,
)
from docling_postprocess.pf21_pipeline import (
    consecutive_heading_runs,
    semantic_diff,
    targeted_regression,
)


def block(number, text, label="text"):
    return {
        "block_id": f"P0001-B{number:03d}", "kind": "text", "text": text,
        "parser_label": label, "bbox_norm": [.1, number * .02, .9, number * .02 + .01],
        "flags": [],
    }


def page(blocks, number=1, page_type=None):
    for item in blocks:
        item["block_id"] = f"P{number:04d}-" + item["block_id"].split("-")[-1]
    result = {"page_no": number, "blocks": blocks}
    if page_type:
        result["page_type"] = page_type
    return result


def chapter_fixture(number=29):
    texts = [
        "7.1.招标文件由《通用本》和《专用本》组成，包括下列内容：",
        "第1章 资格预审公告/招标公告/投标邀请书", "第2章 投标须知及投标须知前附表",
        "第3章 评标办法和标准", "第4章 合同条款和格式", "第5章 技术标准和设计要求",
        "第6章 设计有关资料", "第7章 投标文件格式", "7.2.除本投标须知第7.1款所述……",
    ]
    return page([block(i + 3, text) for i, text in enumerate(texts)], number)


class EnumerationGuardTests(unittest.TestCase):
    def test_page29_chapter_enumeration_is_demoted(self):
        final = build_final_page(chapter_fixture())
        items = {e["source_ids"][0]: e for e in final["ordered_elements"]}
        for i in range(4, 11):
            self.assertEqual(items[f"P0029-B{i:03d}"]["semantic_type"], "text")
            self.assertEqual(items[f"P0029-B{i:03d}"]["decision"], "BODY_ENUMERATION_LIST")

    def test_real_chapter_heading_stays_heading(self):
        final = build_final_page(page([block(1, "第1章 招标公告"), block(2, "正文内容。")]))
        self.assertEqual(final["ordered_elements"][0]["semantic_type"], "heading")

    def test_real_chapter_and_section_stay_headings(self):
        final = build_final_page(page([block(1, "第2章 投标须知"), block(2, "第1节 投标须知前附表")]))
        self.assertEqual([e["semantic_type"] for e in final["ordered_elements"]], ["heading", "heading"])

    def test_no_colon_does_not_trigger(self):
        p = page([block(1, "下列章节")]+[block(i+2, f"第{i+1}章 标题") for i in range(3)])
        self.assertEqual(detect_structural_enumeration_runs(p), [])

    def test_fewer_than_three_does_not_trigger(self):
        p = page([block(1, "包括："), block(2, "第1章 A"), block(3, "第2章 B")])
        self.assertEqual(detect_structural_enumeration_runs(p), [])

    def test_toc_does_not_trigger(self):
        p = page([block(1, "目录："), block(2, "第1章 A 5"), block(3, "第2章 B 9"), block(4, "第3章 C 12")])
        self.assertEqual(detect_structural_enumeration_runs(p), [])

    def test_explicit_toc_page_type_does_not_trigger(self):
        p = page([block(1, "包括："), block(2, "第1章 A"), block(3, "第2章 B"), block(4, "第3章 C")], page_type="toc")
        self.assertEqual(detect_structural_enumeration_runs(p), [])

    def test_nonconsecutive_numbers_are_conservative(self):
        p = page([block(1, "包括："), block(2, "第1章 A"), block(3, "第3章 B"), block(4, "第4章 C")])
        self.assertEqual(detect_structural_enumeration_runs(p), [])

    def test_section_family_supported(self):
        p = page([block(1, "分为："), block(2, "第三节 A"), block(3, "第四节 B"), block(4, "第五节 C")])
        self.assertEqual(detect_structural_enumeration_runs(p)[0]["family"], "SECTION")

    def test_part_family_supported(self):
        p = page([block(1, "如下："), block(2, "第一部分 A"), block(3, "第二部分 B"), block(4, "第三部分 C")])
        self.assertEqual(detect_structural_enumeration_runs(p)[0]["family"], "PART")

    def test_group_level_source_coverage_is_complete(self):
        p = chapter_fixture()
        self.assertEqual(source_text_coverage(p, build_final_page(p))["coverage"], 1.0)

    def test_semantic_diff_reports_source_ids(self):
        p = chapter_fixture(); old = build_final_page(p)
        for e in old["ordered_elements"]:
            if e["decision"] == "BODY_ENUMERATION_LIST":
                e.update(semantic_type="heading", heading_level=1, decision="EXPLICIT_CHAPTER")
        changes = semantic_diff([old], [build_final_page(p)])
        self.assertEqual([x["source_id"] for x in changes], [f"P0029-B{i:03d}" for i in range(4, 11)])

    def test_targeted_regression_never_calls_api(self):
        p = chapter_fixture(); old = build_final_page(p)
        for e in old["ordered_elements"]:
            if e["decision"] == "BODY_ENUMERATION_LIST":
                e.update(semantic_type="heading", heading_level=1, decision="EXPLICIT_CHAPTER")
        with tempfile.TemporaryDirectory() as folder, patch(
                "docling_postprocess.vlm_client.call_page_structure") as api:
            result = targeted_regression([p], [old], Path(folder), [29])
            self.assertEqual(result["api_calls"], 0)
            api.assert_not_called()

    def test_consecutive_heading_preflight(self):
        final = build_final_page(page([block(i+1, f"第{i+1}章 H") for i in range(5)]))
        self.assertEqual(consecutive_heading_runs([final])[0]["count"], 5)


if __name__ == "__main__":
    unittest.main()
