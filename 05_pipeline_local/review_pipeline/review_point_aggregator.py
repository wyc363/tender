from __future__ import annotations

import json
import re
import unicodedata
from typing import Any


PASS_EVIDENCE_TOP_N = 3


class ReviewPointAggregationError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def _dedupe(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in (None, "") and value not in result:
            result.append(value)
    return result


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    return re.sub(r"\s+", " ", normalized)


def _compact_with_positions(text: str) -> tuple[str, list[int]]:
    compact: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(text):
        if not char.isspace():
            compact.append(char)
            positions.append(index)
    return "".join(compact), positions


def _exact_source_substring(proposed: str, source: str) -> str | None:
    cleaned = str(proposed or "").strip().strip('"“”\'‘’')
    if not cleaned:
        return None
    if cleaned in source:
        return cleaned
    source_compact, positions = _compact_with_positions(source)
    proposed_compact, _ = _compact_with_positions(cleaned)
    start = source_compact.find(proposed_compact)
    if start < 0:
        return None
    end = start + len(proposed_compact) - 1
    return source[positions[start]:positions[end] + 1]


def _source_chunks(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    anchor = candidate.get("anchor_chunk") or {}
    return [anchor, *(candidate.get("context_before") or []), *(candidate.get("context_after") or [])]


def _map_evidence(candidate: dict[str, Any], proposed: str) -> dict[str, Any] | None:
    anchor = candidate.get("anchor_chunk") or {}
    anchor_id = candidate.get("anchor_chunk_id") or anchor.get("chunk_id")
    anchor_order = int(anchor.get("source_order") or candidate.get("source_order") or 0)
    matches: list[dict[str, Any]] = []
    for chunk in _source_chunks(candidate):
        source_text = str(chunk.get("text") or "")
        exact = _exact_source_substring(proposed, source_text)
        if exact is None:
            continue
        source_order = int(chunk.get("source_order") or 0)
        matches.append({
            "chunk_id": chunk.get("chunk_id"),
            "text": exact,
            "is_anchor": chunk.get("chunk_id") == anchor_id,
            "source_order": source_order,
            "page_numbers": list(chunk.get("page_numbers") or []),
        })
    matches.sort(key=lambda row: (
        not row["is_anchor"], abs(row["source_order"] - anchor_order), row["source_order"]
    ))
    return matches[0] if matches else None


def _parse_raw_is_pass(raw_output: str) -> bool | None:
    """Recover the model's boolean before Judge grounding/output guards run."""
    raw = str(raw_output or "")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S | re.I)
    json_text = fenced.group(1) if fenced else raw[raw.find("{"):raw.rfind("}") + 1]
    if json_text:
        try:
            parsed = json.loads(json_text)
            if isinstance(parsed, dict):
                value = parsed.get("is_pass")
                if type(value) is bool:
                    return value
                token = str(value).strip().lower()
                if token in {"true", "pass", "通过", "合规", "不违规"}:
                    return True
                if token in {"false", "fail", "不通过", "违规", "不合规"}:
                    return False
        except json.JSONDecodeError:
            pass
    match = re.search(
        r"(?:^|\n)\s*(?:[-*]\s*)?(?:is_pass|Is Pass)\s*[:：]\s*([^\n]+)",
        raw,
        re.I,
    )
    if not match:
        return None
    token = match.group(1).strip().strip('"\'').lower()
    if token in {"true", "pass", "通过", "合规", "不违规"}:
        return True
    if token in {"false", "fail", "不通过", "违规", "不合规"}:
        return False
    return None


def _raw_result(prediction: dict[str, Any]) -> str:
    candidate_id = str(prediction.get("candidate_id") or "<missing>")
    if prediction.get("technical_error"):
        raise ReviewPointAggregationError(
            "AGGREGATION_TECHNICAL_FAILURE",
            f"raw prediction {candidate_id} has technical_error",
        )
    business = prediction.get("审查结果")
    business_result = "FAIL" if business == "违规" else "PASS" if business == "不违规" else None
    raw_is_pass = _parse_raw_is_pass(str(prediction.get("raw_output") or ""))
    raw_result = "PASS" if raw_is_pass is True else "FAIL" if raw_is_pass is False else None
    if business_result and raw_result and business_result != raw_result:
        raise ReviewPointAggregationError(
            "AGGREGATION_RAW_RESULT_CONFLICT",
            f"raw prediction {candidate_id} business={business_result} raw_output={raw_result}",
        )
    if business_result:
        return business_result
    if raw_result:
        return raw_result
    raise ReviewPointAggregationError(
        "AGGREGATION_RAW_RESULT_INVALID",
        f"raw prediction {candidate_id} has no parseable PASS/FAIL; "
        f"审查结果={business!r}, parse_status={prediction.get('parse_status')!r}",
    )


def _prediction_reason(prediction: dict[str, Any]) -> str:
    reason = str(
        prediction.get("判断理由")
        or prediction.get("model_reason")
        or prediction.get("reason")
        or ""
    ).strip()
    if not reason:
        raise ReviewPointAggregationError(
            "AGGREGATION_REASON_MISSING",
            f"raw prediction {prediction.get('candidate_id')} has no reason",
        )
    return reason


def _rule_id_from_query(query_id: str | None) -> str | None:
    match = re.fullmatch(r"RISK_(\d+)", str(query_id or ""))
    return f"{match.group(1)}.0" if match else None


def candidate_terms(review_point: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    """Reuse the existing strongest risk-query to violation-term mapping."""
    rule_to_term = dict(zip(review_point.get("rule_ids", []), review_point.get("violation_terms", [])))
    risk_hits = [
        hit for hit in (candidate.get("bm25_hits") or [])
        if hit.get("query_channel") == "risk" and _rule_id_from_query(hit.get("query_id")) in rule_to_term
    ]
    if not risk_hits:
        return []
    strongest = max(risk_hits, key=lambda hit: float(hit.get("score") or 0))
    term = rule_to_term.get(_rule_id_from_query(strongest.get("query_id")))
    return [term] if term else []


def _numbered_reason(prefix: str, reasons: list[str]) -> str:
    parts = [f"{index}. {reason.rstrip('；。 ')}" for index, reason in enumerate(reasons, 1)]
    return prefix + "；".join(parts) + "。"


def _validate_join(
    candidates: list[dict[str, Any]], predictions: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if not candidates:
        raise ReviewPointAggregationError("AGGREGATION_INPUT_EMPTY", "retrieval candidates are empty")
    candidate_ids = [str(row.get("candidate_id") or "") for row in candidates]
    prediction_ids = [str(row.get("candidate_id") or "") for row in predictions]
    if any(not value for value in candidate_ids + prediction_ids):
        raise ReviewPointAggregationError("AGGREGATION_ID_MISSING", "candidate_id is missing")
    if len(set(candidate_ids)) != len(candidate_ids) or len(set(prediction_ids)) != len(prediction_ids):
        raise ReviewPointAggregationError("AGGREGATION_DUPLICATE_ID", "candidate_id is not unique")
    missing = sorted(set(candidate_ids) - set(prediction_ids))
    extra = sorted(set(prediction_ids) - set(candidate_ids))
    if missing or extra:
        raise ReviewPointAggregationError(
            "AGGREGATION_INPUT_INCOMPLETE",
            f"missing_predictions={missing}; extra_predictions={extra}",
        )
    try:
        ordered = sorted(candidates, key=lambda row: int(row.get("rank")))
    except (TypeError, ValueError) as exc:
        raise ReviewPointAggregationError("AGGREGATION_RANK_INVALID", "candidate rank is missing or invalid") from exc
    return ordered, {row["candidate_id"]: row for row in predictions}


def _primary_page(pages: list[Any], candidate_id: str) -> int:
    if not pages:
        raise ReviewPointAggregationError(
            "AGGREGATION_PAGE_MISSING", f"candidate {candidate_id} evidence has no page"
        )
    try:
        return int(pages[0])
    except (TypeError, ValueError) as exc:
        raise ReviewPointAggregationError(
            "AGGREGATION_PAGE_INVALID", f"candidate {candidate_id} page is invalid: {pages[0]!r}"
        ) from exc


def aggregate_review_point(
    retrieval_candidates: list[dict[str, Any]],
    raw_predictions: list[dict[str, Any]],
    review_point: dict[str, Any],
    *,
    pass_evidence_top_n: int = PASS_EVIDENCE_TOP_N,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Aggregate raw candidate judgments into one deterministic review-point result."""
    if pass_evidence_top_n < 1:
        raise ValueError("pass_evidence_top_n must be positive")
    required_metadata = ("review_point_id", "index", "name", "type", "rule", "risk_level")
    missing_metadata = [key for key in required_metadata if review_point.get(key) in (None, "")]
    if missing_metadata:
        raise ReviewPointAggregationError(
            "AGGREGATION_METADATA_INCOMPLETE", f"missing review point metadata: {missing_metadata}"
        )

    candidates, prediction_by_id = _validate_join(retrieval_candidates, raw_predictions)
    statuses: dict[str, str] = {}
    for candidate in candidates:
        candidate_id = candidate["candidate_id"]
        if candidate.get("rule_id") not in (None, review_point["review_point_id"]):
            raise ReviewPointAggregationError(
                "AGGREGATION_RULE_MISMATCH",
                f"candidate {candidate_id} rule_id={candidate.get('rule_id')!r}",
            )
        statuses[candidate_id] = _raw_result(prediction_by_id[candidate_id])

    fail_candidates = [row for row in candidates if statuses[row["candidate_id"]] == "FAIL"]
    pass_candidates = [row for row in candidates if statuses[row["candidate_id"]] == "PASS"]
    selected: list[dict[str, Any]] = []
    duplicate_ids: list[str] = []
    used_chunk_text: set[tuple[str, str]] = set()
    used_page_text: set[tuple[int, str]] = set()
    unmapped_evidence: list[dict[str, Any]] = []

    if fail_candidates:
        terms: list[str] = []
        for candidate in fail_candidates:
            candidate_id = candidate["candidate_id"]
            prediction = prediction_by_id[candidate_id]
            evidence_values = prediction.get("违规原文") or prediction.get("模型候选违规原文") or []
            if not isinstance(evidence_values, list) or not evidence_values:
                raise ReviewPointAggregationError(
                    "AGGREGATION_EVIDENCE_MISSING", f"raw FAIL {candidate_id} has no evidence list"
                )
            candidate_selected = False
            for evidence_index, proposed in enumerate(evidence_values, 1):
                mapped = _map_evidence(candidate, str(proposed))
                if mapped is None:
                    unmapped_evidence.append({
                        "candidate_id": candidate_id,
                        "evidence_index": evidence_index,
                        "char_count": len(str(proposed)),
                    })
                    continue
                page = _primary_page(mapped["page_numbers"], candidate_id)
                chunk_id = str(mapped.get("chunk_id") or "")
                normalized_text = _normalize_text(mapped["text"])
                chunk_text_key = (chunk_id, normalized_text)
                page_text_key = (page, normalized_text)
                if (chunk_id and chunk_text_key in used_chunk_text) or page_text_key in used_page_text:
                    duplicate_ids.append(candidate_id)
                    continue
                selected.append({
                    "candidate_id": candidate_id,
                    "rank": int(candidate["rank"]),
                    "chunk_id": chunk_id,
                    "text": mapped["text"],
                    "page": page,
                    "source_pages": mapped["page_numbers"],
                    "reason": _prediction_reason(prediction),
                })
                if chunk_id:
                    used_chunk_text.add(chunk_text_key)
                used_page_text.add(page_text_key)
                candidate_selected = True
            if candidate_selected:
                for term in candidate_terms(review_point, candidate):
                    if term not in terms:
                        terms.append(term)
        if not selected:
            raise ReviewPointAggregationError(
                "AGGREGATION_EVIDENCE_UNMAPPED",
                "raw FAIL decisions exist but no model evidence can be mapped to candidate source text",
            )
        is_pass = False
        reason = _numbered_reason("", [row["reason"] for row in selected])
    else:
        terms = []
        for candidate in pass_candidates:
            candidate_id = candidate["candidate_id"]
            anchor = candidate.get("anchor_chunk") or {}
            text = str(anchor.get("text") or candidate.get("text") or "")
            if not text:
                raise ReviewPointAggregationError(
                    "AGGREGATION_EVIDENCE_MISSING", f"raw PASS {candidate_id} has no anchor text"
                )
            pages = list(anchor.get("page_numbers") or candidate.get("page_numbers") or [])
            page = _primary_page(pages, candidate_id)
            chunk_id = str(anchor.get("chunk_id") or candidate.get("anchor_chunk_id") or candidate.get("chunk_id") or "")
            normalized_text = _normalize_text(text)
            chunk_text_key = (chunk_id, normalized_text)
            page_text_key = (page, normalized_text)
            if (chunk_id and chunk_text_key in used_chunk_text) or page_text_key in used_page_text:
                duplicate_ids.append(candidate_id)
                continue
            selected.append({
                "candidate_id": candidate_id,
                "rank": int(candidate["rank"]),
                "chunk_id": chunk_id,
                "text": text,
                "page": page,
                "source_pages": pages,
                "reason": _prediction_reason(prediction_by_id[candidate_id]),
            })
            if chunk_id:
                used_chunk_text.add(chunk_text_key)
            used_page_text.add(page_text_key)
            if len(selected) >= pass_evidence_top_n:
                break
        if not selected:
            raise ReviewPointAggregationError("AGGREGATION_EVIDENCE_EMPTY", "no unique PASS evidence remains")
        is_pass = True
        reason = _numbered_reason(
            "经审查，未发现本审查点相关违规情形。主要判断依据：",
            [row["reason"] for row in selected],
        )

    policy = "\n".join(_dedupe(list(review_point.get("policy_sources") or [])))
    result = {
        "review_point_id": review_point["review_point_id"],
        "index": int(review_point["index"]),
        "candidate_count": len(candidates),
        "raw_pass_count": len(pass_candidates),
        "raw_fail_count": len(fail_candidates),
        "is_pass": is_pass,
        "term": terms,
        "reason": reason,
        "text": [row["text"] for row in selected],
        "page": [row["page"] for row in selected],
        "name": review_point["name"],
        "rule": review_point["rule"],
        "policy": policy,
        "type": review_point["type"],
        "risk_level": review_point["risk_level"],
    }
    audit = {
        "review_point_id": review_point["review_point_id"],
        "index": int(review_point["index"]),
        "candidate_count": len(candidates),
        "raw_pass_count": len(pass_candidates),
        "raw_fail_count": len(fail_candidates),
        "selected_candidate_ids": _dedupe([row["candidate_id"] for row in selected]),
        "selected_fail_candidate_ids": _dedupe(
            [row["candidate_id"] for row in selected if statuses[row["candidate_id"]] == "FAIL"]
        ),
        "selected_pass_candidate_ids": _dedupe(
            [row["candidate_id"] for row in selected if statuses[row["candidate_id"]] == "PASS"]
        ),
        "deduplicated_candidate_ids": _dedupe(duplicate_ids),
        "unmapped_fail_evidence": unmapped_evidence,
        "selected_evidence": [
            {key: row[key] for key in ("candidate_id", "rank", "chunk_id", "page", "source_pages")}
            for row in selected
        ],
        "external_is_pass": is_pass,
        "pass_evidence_top_n": pass_evidence_top_n,
    }
    return result, audit


def client_result_item(review_point_result: dict[str, Any]) -> dict[str, Any]:
    return {key: review_point_result[key] for key in (
        "term", "reason", "text", "page", "is_pass", "name", "rule",
        "policy", "type", "risk_level", "index",
    )}
