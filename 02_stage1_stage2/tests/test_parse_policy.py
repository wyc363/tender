from document_processing.parse_planner import build_parse_plan


def test_contract_is_context_only_and_determination_is_triggered():
    section_map = {
        "doc_id": "DOC016", "file_name": "x.pdf", "file_path": "x.pdf", "page_count": 10,
        "sections": [
            {"section_id": "CH01", "section_role": "ANNOUNCEMENT", "pdf_page_start": 1, "pdf_page_end": 1, "start_page_idx": 0, "end_page_idx": 0, "confidence": 1.0},
            {"section_id": "CH02", "section_role": "QUALIFICATION", "pdf_page_start": 2, "pdf_page_end": 2, "start_page_idx": 1, "end_page_idx": 1, "confidence": 1.0},
            {"section_id": "CH03", "section_role": "EVALUATION", "pdf_page_start": 3, "pdf_page_end": 5, "start_page_idx": 2, "end_page_idx": 4, "confidence": 1.0},
            {"section_id": "CH04", "section_role": "DETERMINATION", "pdf_page_start": 6, "pdf_page_end": 7, "start_page_idx": 5, "end_page_idx": 6, "confidence": 1.0},
            {"section_id": "CH05", "section_role": "CONTRACT", "pdf_page_start": 8, "pdf_page_end": 10, "start_page_idx": 7, "end_page_idx": 9, "confidence": 1.0},
        ],
        "keyword_page_index": {"pages": []},
    }
    policy = {
        "review_item": "x", "official_rule_ids": ["1201"],
        "primary_sections": ["EVALUATION"], "required_sections": ["ANNOUNCEMENT", "QUALIFICATION"],
        "triggered_sections": ["DETERMINATION", "AWARD_SELECTION"], "conditional_sections": ["BIDDER_NOTICE_TABLE"],
        "context_on_demand_sections": ["CONTRACT"],
    }
    plan = build_parse_plan(section_map, "regional_industry_award", policy)
    roles = {item["role"] for item in plan["ranges"]}
    assert roles == {"ANNOUNCEMENT", "QUALIFICATION", "EVALUATION", "DETERMINATION"}
    assert plan["context_on_demand_sections_located"] == ["CONTRACT"]
