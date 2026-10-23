from __future__ import annotations

import pytest

from review_pipeline.review_point_aggregator import (
    ReviewPointAggregationError,
    aggregate_review_point,
)


REVIEW_POINT = {
    "review_point_id": "RULE",
    "index": 12,
    "name": "地域/行业业绩奖项及外部荣誉作为招投标加分、中标条件",
    "type": "资质加分审查",
    "rule": "不得将地域、行业奖项作为加分或中标条件",
    "risk_level": "高",
    "policy_sources": ["政策依据"],
    "rule_ids": ["1.0", "2.0"],
    "violation_terms": ["违规条目一", "违规条目二"],
}


def candidate(candidate_id: str, rank: int, chunk_id: str, text: str, page: int,
              *, risk: str = "RISK_1", context_before: list[dict] | None = None) -> dict:
    return {
        "candidate_id": candidate_id,
        "rule_id": "RULE",
        "rank": rank,
        "anchor_chunk": {
            "chunk_id": chunk_id,
            "source_order": rank,
            "text": text,
            "page_numbers": [page],
        },
        "context_before": context_before or [],
        "context_after": [],
        "bm25_hits": [{"query_channel": "risk", "query_id": risk, "score": 9.0}],
    }


def prediction(candidate_id: str, result: str, reason: str, evidence: list[str] | None = None,
               *, raw_is_pass: bool | None = None) -> dict:
    if raw_is_pass is None:
        raw_is_pass = result == "不违规"
    return {
        "candidate_id": candidate_id,
        "审查结果": result,
        "判断理由": reason,
        "违规原文": evidence or [],
        "parse_status": "OK",
        "technical_error": None,
        "raw_output": f'- is_pass: {str(raw_is_pass).lower()}\n- reason: {reason}\n- text: []',
    }


def test_pass_uses_top_three_unique_candidates_in_rank_order():
    candidates = [candidate(f"K{i}", i, f"C{i}", f"片段{i}", i) for i in range(1, 5)]
    predictions = [prediction(f"K{i}", "不违规", f"理由{i}") for i in range(1, 5)]

    result, audit = aggregate_review_point(candidates, predictions, REVIEW_POINT)

    assert result["is_pass"] is True
    assert result["index"] == 12
    assert result["term"] == []
    assert result["text"] == ["片段1", "片段2", "片段3"]
    assert result["page"] == [1, 2, 3]
    assert audit["selected_pass_candidate_ids"] == ["K1", "K2", "K3"]


def test_fail_uses_only_raw_fail_evidence_and_deduplicates_source_chunk():
    shared = {"chunk_id": "CTX", "source_order": 8, "text": "本项目要求获得本省奖项。", "page_numbers": [22]}
    candidates = [
        candidate("K1", 1, "C1", "正常条款", 10),
        candidate("K2", 2, "C2", "关联条款", 23, context_before=[shared]),
        candidate("K3", 3, "C3", "另一关联条款", 24, context_before=[shared]),
    ]
    predictions = [
        prediction("K1", "不违规", "未发现违规"),
        prediction("K2", "违规", "将本省奖项作为条件", ["本项目要求获得本省奖项。"]),
        prediction("K3", "违规", "同一事实重复召回", ["本项目要求获得本省奖项。"]),
    ]

    result, audit = aggregate_review_point(candidates, predictions, REVIEW_POINT)

    assert result["is_pass"] is False
    assert result["text"] == ["本项目要求获得本省奖项。"]
    assert result["page"] == [22]
    assert result["term"] == ["违规条目一"]
    assert audit["selected_fail_candidate_ids"] == ["K2"]
    assert audit["selected_pass_candidate_ids"] == []
    assert audit["deduplicated_candidate_ids"] == ["K3"]


def test_same_chunk_with_different_evidence_text_keeps_both():
    source = "第一项：要求获得本省奖项。\n第二项：要求行业协会推荐。"
    candidates = [candidate("K1", 1, "C1", source, 22)]
    predictions = [prediction(
        "K1", "违规", "存在两项独立违规",
        ["第一项：要求获得本省奖项。", "第二项：要求行业协会推荐。"],
        raw_is_pass=False,
    )]

    result, audit = aggregate_review_point(candidates, predictions, REVIEW_POINT)

    assert result["text"] == ["第一项：要求获得本省奖项。", "第二项：要求行业协会推荐。"]
    assert result["page"] == [22, 22]
    assert audit["deduplicated_candidate_ids"] == []


def test_same_page_and_same_evidence_text_deduplicates_across_chunks():
    text = "要求获得本省奖项。"
    candidates = [
        candidate("K1", 1, "C1", text, 22),
        candidate("K2", 2, "C2", text, 22),
    ]
    predictions = [
        prediction("K1", "违规", "理由一", [text], raw_is_pass=False),
        prediction("K2", "违规", "理由二", [text], raw_is_pass=False),
    ]

    result, audit = aggregate_review_point(candidates, predictions, REVIEW_POINT)

    assert result["text"] == [text]
    assert result["page"] == [22]
    assert audit["deduplicated_candidate_ids"] == ["K2"]


@pytest.mark.parametrize("raw_is_pass,expected", [(True, True), (False, False)])
def test_guard_overwritten_result_recovers_original_model_boolean(raw_is_pass, expected):
    source = "本项目将省级奖项作为加分条件。"
    raw = prediction(
        "K1", "结果无效", "模型已形成明确结论",
        [] if raw_is_pass else [source],
        raw_is_pass=raw_is_pass,
    )
    raw["parse_status"] = "HIGH_RISK_TRIGGER_MISSED" if raw_is_pass else "SPECULATIVE_VIOLATION"
    raw["模型候选违规原文"] = [] if raw_is_pass else [source]

    result, _ = aggregate_review_point([candidate("K1", 1, "C1", source, 1)], [raw], REVIEW_POINT)

    assert result["is_pass"] is expected


def test_candidate_prediction_join_must_be_complete():
    with pytest.raises(ReviewPointAggregationError) as captured:
        aggregate_review_point(
            [candidate("K1", 1, "C1", "片段", 1)],
            [],
            REVIEW_POINT,
        )
    assert captured.value.code == "AGGREGATION_INPUT_INCOMPLETE"


@pytest.mark.parametrize("change,code", [
    ({"parse_status": "INVALID", "审查结果": "结果无效", "raw_output": "not-json"},
     "AGGREGATION_RAW_RESULT_INVALID"),
    ({"technical_error": "timeout"}, "AGGREGATION_TECHNICAL_FAILURE"),
])
def test_invalid_or_technical_raw_result_is_not_mapped_to_boolean(change, code):
    raw = prediction("K1", "不违规", "理由")
    raw.update(change)
    with pytest.raises(ReviewPointAggregationError) as captured:
        aggregate_review_point([candidate("K1", 1, "C1", "片段", 1)], [raw], REVIEW_POINT)
    assert captured.value.code == code
