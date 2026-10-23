from document_processing.parse_planner import build_parse_plan, normalize_physical_jobs
from document_processing.section_locator import is_heading_shape, locate_sections


def _scan(texts):
    return {"doc_id": "DOC012", "file_name": "x.pdf", "file_path": "x.pdf", "file_sha256": "0"*64,
            "page_count": len(texts), "pages": [{"pdf_page": i+1, "page_idx": i, "text": t} for i, t in enumerate(texts)]}


def test_body_reference_is_not_heading():
    assert not is_heading_shape("符合第八章《投标文件格式》中要求的材料", "投标文件格式")
    assert not is_heading_shape("见第三章评标办法中规定的评分标准", "评标办法")
    assert is_heading_shape("第三章 评标办法（综合评估法）", "评标办法")


def test_first_section_bidder_table_and_body_are_separate():
    result = locate_sections(_scan(["第二章 投标人须知", "第一节 投标人须知前附表", "表格内容", "第二节 投标人须知", "正文", "第三章 评标办法", "内容"]))
    roles = {s["section_role"]: s for s in result["sections"]}
    assert roles["BIDDER_NOTICE_TABLE"]["pdf_page_start"] == 2
    assert roles["BIDDER_NOTICE_TABLE"]["pdf_page_end"] == 3
    assert roles["BIDDER_NOTICE"]["pdf_page_start"] == 4


def test_physical_jobs_merge_overlap_and_adjacency():
    logical = [{"range_id": "R01", "role": "A", "mineru_start": 1, "mineru_end": 3},
               {"range_id": "R02", "role": "B", "mineru_start": 3, "mineru_end": 5},
               {"range_id": "R03", "role": "C", "mineru_start": 6, "mineru_end": 7}]
    jobs = normalize_physical_jobs(logical, 0)
    assert len(jobs) == 1 and jobs[0]["source_range_ids"] == ["R01", "R02", "R03"]


def test_weak_terms_do_not_trigger_conditional():
    section_map = {"doc_id": "D", "file_name": "x", "file_path": "x", "page_count": 3,
                   "sections": [{"section_id": "CH1", "section_role": "BIDDER_NOTICE_TABLE", "start_page_idx": 0, "end_page_idx": 2, "confidence": 1}],
                   "keyword_page_index": {"pages": [{"page_idx": 1, "section_role": "BIDDER_NOTICE_TABLE", "hits": {"得分": 1, "中标": 1}}]}}
    policy = {"review_item": "x", "official_rule_ids": [], "conditional_sections": ["BIDDER_NOTICE_TABLE"],
              "range_anchor_terms": ["业绩", "奖项"], "primary_sections": [], "required_sections": [], "triggered_sections": []}
    assert build_parse_plan(section_map, "x", policy)["logical_ranges"] == []
