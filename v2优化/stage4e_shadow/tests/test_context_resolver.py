from __future__ import annotations

import pytest

from rp12_stage4e.context_resolver import ContextResolver, ContextResolutionError, GapRequest


def chunk(i, text, *, doc="DOC1", table=None, heading=("评标办法",), pages=None):
    return {
        "chunk_id": f"{doc}-C{i:06d}", "document_id": doc, "source_order": i, "text": text,
        "heading_path": list(heading), "page_numbers": pages or [i], "source_block_ids": [f"B{i}"],
        "source_table_ids": [table] if table else [],
    }


def test_rejects_cross_document_chunk_input():
    with pytest.raises(ContextResolutionError, match="CROSS_DOCUMENT"):
        ContextResolver("DOC1", [chunk(1, "x"), chunk(2, "y", doc="DOC2")])


def test_missing_anchor_fails_closed():
    resolver = ContextResolver("DOC1", [chunk(1, "x")])
    with pytest.raises(ContextResolutionError, match="ANCHOR_CHUNK_NOT_FOUND"):
        resolver.resolve("CAND", "missing", GapRequest("BUSINESS_EFFECT", ("F01",), ("得分",)))


def test_same_table_cross_page_is_retrieved():
    resolver = ContextResolver("DOC1", [chunk(1, "获奖业绩（0分）", table="T1", pages=[8]), chunk(2, "省级奖得0.5分", table="T1", pages=[9])])
    result = resolver.resolve("CAND", "DOC1-C000002", GapRequest("SCORING_PARENT_AND_TEMPLATE_ACTIVATION", ("F01",), ("获奖业绩", "得0.5分")))
    assert result["page_numbers"] == [8, 9]
    assert set(result["retrieval_method"]) == {"ANCHOR_ID", "SOURCE_TABLE_ID"}


def test_zero_parent_positive_child_remains_unresolved():
    resolver = ContextResolver("DOC1", [chunk(1, "获奖业绩（0分）", table="T1"), chunk(2, "适用于要求创建省级优质工程，得0.5分", table="T1")])
    result = resolver.resolve("CAND", "DOC1-C000002", GapRequest("SCORING_PARENT_AND_TEMPLATE_ACTIVATION", ("F01",), ("获奖业绩", "得0.5分", "适用于")))
    assert result["context_found"] is True
    assert result["resolved"] is False
    assert result["gap_reason"] == "PARENT_ZERO_CHILD_POSITIVE_CONFLICT_REPRODUCED"


def test_explicit_inactive_resolves_activation():
    resolver = ContextResolver("DOC1", [chunk(1, "获奖业绩（0分） 本项不适用", table="T1"), chunk(2, "模板文字：得0.5分", table="T1")])
    result = resolver.resolve("CAND", "DOC1-C000002", GapRequest("SCORING_PARENT_AND_TEMPLATE_ACTIVATION", ("F01",), ("获奖业绩", "得0.5分")))
    assert result["resolved"] is True


def test_unrelated_table_parent_not_collected():
    resolver = ContextResolver("DOC1", [chunk(1, "获奖业绩（0分）", table="T-OLD", heading=("上一节",)), chunk(10, "子项得0.5分", table="T-NEW", heading=("本节",))])
    result = resolver.resolve("CAND", "DOC1-C000010", GapRequest("SCORING_PARENT_AND_TEMPLATE_ACTIVATION", ("F01",), ("获奖业绩", "得0.5分")))
    assert "DOC1-C000001" not in [x["chunk_id"] for x in result["retrieved_chunks"]]


def test_duplicate_text_deduplicated():
    resolver = ContextResolver("DOC1", [chunk(1, "综合评分相等时投标人优先"), chunk(2, "综合评分相等时投标人优先")])
    result = resolver.resolve("CAND", "DOC1-C000001", GapRequest("TIE_BREAK_OR_BUSINESS_EFFECT", ("F01",), ("综合评分相等",)))
    assert len(result["retrieved_text"]) == 1
    assert result["integrity_checks"]["duplicate_text_count"] == 0


def test_saved_text_integrity_check():
    resolver = ContextResolver("DOC1", [chunk(1, "作为资格认定和业绩加分依据")])
    result = resolver.resolve("CAND", "DOC1-C000001", GapRequest("BUSINESS_EFFECT", ("F01",), ("资格认定", "加分依据")))
    assert result["integrity_checks"]["text_verbatim_in_saved_chunks"] is True


def test_gap_inference_does_not_expand_rule_coverage_only_without_signal():
    resolver = ContextResolver("DOC1", [chunk(1, "普通文本")])
    requests = resolver.infer_gaps([{"fact_id":"F01","atomic_evidence":"普通文本"}], ["RULE_PACK_COVERAGE_INCOMPLETE"])
    assert requests[0].gap_type == "BUSINESS_EFFECT"
    result = resolver.resolve("CAND", "DOC1-C000001", requests[0])
    assert result["context_found"] is False


def test_model_activation_annotation_can_be_targeted_without_model_call():
    resolver = ContextResolver("DOC1", [chunk(1, "本项目适用，省级奖得0.5分", table="T1")])
    result = resolver.resolve("CAND", "DOC1-C000001", GapRequest("SCORING_PARENT_AND_TEMPLATE_ACTIVATION", ("F01",), ("本项目适用", "得0.5分")))
    assert result["resolved"] is True
