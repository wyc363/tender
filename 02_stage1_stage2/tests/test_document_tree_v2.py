from src.document_processing.document_tree_v2 import split_text_by_verified_headings


def test_outline_aware_splitter_only_uses_verified_heading():
    parts = split_text_by_verified_headings("前文 第一章 总则 正文 第二章 范围 内容", ["第一章 总则", "第二章 范围"])
    assert [x["heading"] for x in parts] == [None, "第一章 总则", "第二章 范围"]


def test_outline_aware_splitter_does_not_guess():
    assert split_text_by_verified_headings("1.1 看似标题但未验证", []) == [{"text": "1.1 看似标题但未验证", "heading": None}]
