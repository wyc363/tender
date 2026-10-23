from document_processing.section_locator import locate_sections, normalize_text


def _scan(texts):
    return {
        "doc_id": "DOC001", "file_name": "x.pdf", "file_path": "x.pdf", "file_sha256": "0" * 64,
        "page_count": len(texts), "pages": [{"pdf_page": i + 1, "page_idx": i, "text": text} for i, text in enumerate(texts)]
    }


def test_normalize_heading_variants():
    variants = ["第 3 章 评标办法", "第三章 评标办法", "第3章评标办法"]
    assert all("评标办法" in normalize_text(value) for value in variants)


def test_directory_false_positive_prefers_real_heading():
    result = locate_sections(_scan([
        "目录\n第一章 招标公告 …… 5\n第二章 投标人须知 …… 12\n第三章 评标办法 …… 74\n第四章 合同条款 …… 90",
        "第三章 评标办法（综合评估法）\n评标委员会按照本章规定的方法、评审因素和标准进行评审。" * 8,
        "第四章 合同条款及格式\n本合同条款适用于本工程。" * 8,
    ]))
    evaluation = next(section for section in result["sections"] if section["section_role"] == "EVALUATION")
    assert evaluation["pdf_page_start"] == 2


def test_determination_is_separate_from_evaluation():
    result = locate_sections(_scan([
        "第三章 评标办法\n评标委员会按照本章规定进行评审。" * 10,
        "第四章 定标办法\n定标委员会按照本章规定确定中标人。" * 10,
        "第五章 合同条款及格式\n中标后履约要求。" * 10,
    ]), ["定标", "中标"])
    roles = {section["section_role"]: section for section in result["sections"]}
    assert roles["EVALUATION"]["pdf_page_end"] == 1
    assert roles["DETERMINATION"]["pdf_page_start"] == 2
    assert result["keyword_page_index"]["purpose"] == "range_expansion_only_not_violation_judgment"
