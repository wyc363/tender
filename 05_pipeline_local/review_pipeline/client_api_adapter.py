from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .client_enrichment import enrich_confirmed_failure
from .review_point_aggregator import (
    ReviewPointAggregationError,
    aggregate_review_point,
    client_result_item,
)
from .rule_catalog import RuleCatalogError, find_review_point, load_rule_catalog
from .utils import read_json, read_jsonl, write_json


DEFAULT_SCOPE = "切分审核"


class ClientApiAdapterError(RuntimeError):
    pass


def _dedupe(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in (None, "") and value not in result:
            result.append(value)
    return result


def _rule_id_from_query(query_id: str | None) -> str | None:
    match = re.fullmatch(r"RISK_(\d+)", str(query_id or ""))
    return f"{match.group(1)}.0" if match else None


def _matched_terms(
    review_point: dict[str, Any],
    findings: list[dict[str, Any]],
    retrieval_candidates: list[dict[str, Any]],
) -> list[str]:
    """Select the strongest retrieval leaf for each confirmed FAIL finding.

    REVIEW findings are intentionally excluded because they are not confirmed
    violations. The current frozen Judge does not return a leaf rule ID, so the
    strongest risk-channel BM25 hit provides a deterministic, auditable mapping.
    """
    candidates = {row.get("candidate_id"): row for row in retrieval_candidates}
    rule_to_term = dict(zip(review_point.get("rule_ids", []), review_point.get("violation_terms", [])))
    terms: list[str] = []
    for finding in findings:
        if finding.get("finding_result") != "FAIL":
            continue
        for prediction_id in finding.get("judge_prediction_ids") or []:
            candidate = candidates.get(prediction_id)
            if not candidate:
                continue
            risk_hits = [
                hit for hit in (candidate.get("bm25_hits") or [])
                if hit.get("query_channel") == "risk" and _rule_id_from_query(hit.get("query_id")) in rule_to_term
            ]
            if not risk_hits:
                continue
            strongest = max(risk_hits, key=lambda hit: float(hit.get("score") or 0))
            term = rule_to_term.get(_rule_id_from_query(strongest.get("query_id")))
            if term and term not in terms:
                terms.append(term)
    return terms


def _review_reason(
    findings: list[dict[str, Any]],
    validated_predictions: list[dict[str, Any]],
) -> str:
    prediction_ids = {
        prediction_id
        for finding in findings
        for prediction_id in (finding.get("judge_prediction_ids") or [])
    }
    related = [
        row for row in validated_predictions
        if row.get("prediction_id") in prediction_ids or row.get("candidate_id") in prediction_ids
    ]
    errors = {
        str(error)
        for row in related
        for error in (row.get("validation_errors") or [])
    }
    model_reasons = _dedupe([finding.get("reason") for finding in findings])
    base = "\n\n".join(str(value) for value in model_reasons if value)
    if not base:
        base = "模型未形成完整的审查原因。"
    failed_checks = sorted(errors) or ["OUTPUT_VALIDATION_FAILED"]
    return (
        f"{base}\n\n自动校验说明：未通过 {', '.join(failed_checks)} 校验，"
        "本项保留为 REVIEW，需人工复核。"
    )


def _reason(
    document_result: dict[str, Any],
    legacy_final: dict[str, Any],
    findings: list[dict[str, Any]],
    validated_predictions: list[dict[str, Any]],
) -> str:
    result = document_result.get("result")
    if result == "REVIEW":
        return _review_reason(findings, validated_predictions)
    reasons = _dedupe([row.get("reason") for row in findings])
    if reasons:
        value = "\n\n".join(reasons)
    else:
        value = str(legacy_final.get("说明") or "经审查，未发现违规情形。")
    return value


def _interface_is_pass(
    internal_result: str,
    related_predictions: list[dict[str, Any]],
) -> bool:
    """Map the internal three-state result to the client's required boolean.

    REVIEW keeps its internal status and explanation.  Because the client
    contract has no REVIEW value, its boolean follows the model's initial
    judgment.  A mixed REVIEW containing any initial FAIL remains false.
    """
    normalized = str(internal_result or "").upper()
    if normalized == "PASS":
        return True
    if normalized == "FAIL":
        return False
    raw_results = {
        str(row.get("raw_result") or "").upper()
        for row in related_predictions
        if row.get("raw_result")
    }
    if "FAIL" in raw_results:
        return False
    if "PASS" in raw_results:
        return True
    return False


def _candidate_terms(
    review_point: dict[str, Any],
    candidate: dict[str, Any],
    is_pass: bool,
) -> list[str]:
    """Map one non-passing retrieval candidate to its strongest risk rule."""
    if is_pass:
        return []
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


def _candidate_reason(prediction: dict[str, Any]) -> str:
    """Keep the model's per-candidate reason and expose validation uncertainty."""
    reason = str(
        prediction.get("model_reason")
        or prediction.get("reason")
        or "模型未形成完整的审查原因。"
    )
    if str(prediction.get("validated_result") or "").upper() != "REVIEW":
        return reason
    errors = sorted({str(value) for value in (prediction.get("validation_errors") or []) if value})
    failed_checks = errors or ["OUTPUT_VALIDATION_FAILED"]
    return (
        f"{reason}\n\n自动校验说明：未通过 {', '.join(failed_checks)} 校验，"
        "内部状态为 REVIEW；甲方接口 is_pass 按模型初始判断映射。"
    )


def _prior_enrichment(root: Path) -> tuple[str, dict[str, Any] | None]:
    """Reuse an existing suggestion/audit when rebuilding without an API call."""
    suggestion = ""
    content_path = root / "client_content_result.json"
    if content_path.is_file():
        suggestion = str((read_json(content_path).get("suggestions") or ""))
    audit_path = root / "client_enrichment_audit.json"
    audit = read_json(audit_path) if audit_path.is_file() else None
    if not suggestion and audit:
        try:
            suggestion = str(json.loads(str(audit.get("raw_response") or "{}")).get("suggestion") or "")
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return suggestion, audit


def build_client_api_outputs(
    document_root: Path,
    catalog_path: Path | None = None,
    chat_id: str | None = None,
    judge_env_file: Path | None = None,
    enrich_fail: bool = False,
    pass_evidence_top_n: int = 3,
    model_environment: dict[str, str] | None = None,
    enrichment_settings: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    root = document_root.resolve()
    judge = root / "05_judge"
    document_result_path = judge / "document_review_result.json"
    legacy_final_path = judge / "final_review_result.json"
    if not document_result_path.is_file() or not legacy_final_path.is_file():
        raise ClientApiAdapterError("Judge document result or final result is missing")

    document_result = read_json(document_result_path)
    legacy_final = read_json(legacy_final_path)
    try:
        catalog = load_rule_catalog(catalog_path)
        review_point = find_review_point(
            catalog,
            review_point_id=document_result.get("rule_id"),
            name=document_result.get("review_item"),
        )
    except RuleCatalogError as exc:
        raise ClientApiAdapterError(str(exc)) from exc
    retrieval_path = root / "04_retrieval" / "retrieval_candidates.jsonl"
    predictions_path = judge / "predictions.jsonl"
    if not retrieval_path.is_file() or not predictions_path.is_file():
        raise ClientApiAdapterError("Retrieval candidates or raw Judge predictions are missing")
    retrieval_candidates = read_jsonl(retrieval_path) if retrieval_path.is_file() else []
    raw_predictions = read_jsonl(predictions_path)
    try:
        review_point_result, aggregation_audit = aggregate_review_point(
            retrieval_candidates,
            raw_predictions,
            review_point,
            pass_evidence_top_n=pass_evidence_top_n,
        )
    except ReviewPointAggregationError as exc:
        raise ClientApiAdapterError(str(exc)) from exc
    write_json(root / "review_point_result.json", review_point_result)
    write_json(root / "client_aggregation_audit.json", aggregation_audit)
    item = client_result_item(review_point_result)

    suggestions, prior_audit = _prior_enrichment(root)
    enrichment_audit: dict[str, Any] | None = prior_audit
    model_judged_fail = not review_point_result["is_pass"]
    if model_judged_fail and enrich_fail:
        if not judge_env_file:
            raise ClientApiAdapterError("judge_env_file is required when enrich_fail is enabled")
        _, suggestions, enrichment_audit = enrich_confirmed_failure(
            review_point,
            review_point_result["reason"],
            review_point_result["text"],
            judge_env_file.resolve(),
            validation_state="FAIL",
            model_environment=model_environment,
            enrichment_settings=enrichment_settings,
        )
    content = {
        "result": [item],
        "suggestions": suggestions,
        "scope": DEFAULT_SCOPE,
    }

    usage_path = judge / "R2_Judge_Usage.json"
    usage = read_json(usage_path) if usage_path.is_file() else {}
    run_manifest_path = judge / "run_manifest.json"
    run_manifest = read_json(run_manifest_path) if run_manifest_path.is_file() else {}
    enrichment_prompt = int((enrichment_audit or {}).get("prompt_tokens") or 0)
    enrichment_completion = int((enrichment_audit or {}).get("completion_tokens") or 0)
    response = {
        "id": chat_id or str(document_result.get("document_id") or root.name),
        "model": usage.get("model") or run_manifest.get("model_name") or "",
        "usage": {
            "prompt_tokens": int(usage.get("input_tokens") or 0) + enrichment_prompt,
            "completion_tokens": int(usage.get("output_tokens") or 0) + enrichment_completion,
            "total_tokens": int(usage.get("total_tokens") or 0) + enrichment_prompt + enrichment_completion,
        },
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(content, ensure_ascii=False),
                },
                "finish_reason": "stop",
                "index": 0,
            }
        ],
        "log_file": "pipeline.log",
    }

    content_path = root / "client_content_result.json"
    response_path = root / "client_api_response.json"
    write_json(content_path, content)
    write_json(response_path, response)
    if enrichment_audit:
        write_json(root / "client_enrichment_audit.json", enrichment_audit)
    return content_path, response_path
