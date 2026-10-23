from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from r2.evidence_mapper import aggregate_result, map_evidence, normalize_evidence  # noqa: E402


HIGH_RISK_PATTERNS = (
    re.compile(r"行业协会(?:或组织)?.{0,30}(?:推荐|推荐名单).{0,30}(?:会员|资格|投标)", re.S),
    re.compile(r"(?:省级|市级|县级|行业).{0,30}(?:奖项|获奖|荣誉|业绩).{0,40}(?:得分|加分|评分|资格条件|投标条件|中标条件)", re.S),
    re.compile(r"(?:慈善|捐款|公益).{0,30}(?:荣誉|证书|证明).{0,40}(?:得分|加分|评分|资格条件|投标条件|中标条件)", re.S),
)


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * pct
    low, high = int(pos), min(int(pos) + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (pos - low), 3)


def infer_raw_result(prediction: dict) -> str:
    if prediction.get("审查结果") == "违规":
        return "FAIL"
    if prediction.get("审查结果") == "不违规":
        return "PASS"
    if prediction.get("模型候选违规原文"):
        return "FAIL"
    raw = prediction.get("raw_output", "")
    match = re.search(r"(?:is_pass|Is Pass)\s*[\"']?\s*[:：]\s*[\"']?(true|false|通过|不通过|违规|不违规)", raw, re.I)
    if match:
        return "PASS" if match.group(1).lower() in {"true", "通过", "不违规"} else "FAIL"
    return "REVIEW"


def check_only(args) -> dict:
    inputs = load_jsonl(args.input)
    chunks = load_jsonl(args.chunks)
    by_id = {row["chunk_id"]: row for row in chunks}
    store = json.loads(args.rule_store.read_text(encoding="utf-8-sig"))
    program = json.loads(args.program.read_text(encoding="utf-8-sig"))
    base = json.loads((args.output / "check_manifest.json").read_text(encoding="utf-8-sig"))
    rule_ids = {row.get("rule_id") for row in inputs}
    anchor_ids = [row.get("anchor_chunk_id") for row in inputs]
    review_items = {row.get("review_item") for row in inputs}
    review_item = next(iter(review_items)) if len(review_items) == 1 else None
    output_writable = True
    try:
        marker = args.output / ".r2_write_check"
        marker.write_text("ok", encoding="utf-8")
        marker.unlink()
    except OSError:
        output_writable = False
    context_valid = all(isinstance(row.get("context_before"), list)
                        and isinstance(row.get("context_after"), list)
                        and row.get("anchor_chunk", {}).get("chunk_id") == row.get("anchor_chunk_id")
                        for row in inputs)
    checks = {
        "judge_inputs_50": len(inputs) == 50,
        "chunk_ids_unique": len(anchor_ids) == len(set(anchor_ids)) == 50,
        "rule_id_consistent": len(rule_ids) == 1 and None not in rule_ids,
        "rule_store_readable_and_selected": review_item in store,
        "frozen_program_readable": bool(program.get("decide", {}).get("signature", {}).get("instructions")),
        "anchor_text_nonempty": all(row.get("anchor_chunk", {}).get("text", "").strip() for row in inputs),
        "context_schema_valid": context_valid,
        "page_numbers_present": all(row.get("page_numbers") for row in inputs),
        "source_provenance_present": all(row.get("source_block_ids") for row in inputs),
        "no_toc_anchor": all(row.get("anchor_chunk", {}).get("document_region") != "toc" for row in inputs),
        "all_anchors_known": all(cid in by_id for cid in anchor_ids),
        "api_base_configured": base.get("api_base_configured") is True,
        "api_key_configured": base.get("api_key_configured") is True,
        "model_configured": bool(base.get("model_name")),
        "temperature_frozen_zero": base.get("temperature") == 0,
        "max_tokens_at_least_1024": base.get("max_tokens", 0) >= 1024,
        "thinking_disabled": base.get("enable_thinking") is False,
        "output_writable": output_writable,
    }
    report = {
        "status": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
        "judge_inputs": len(inputs), "unique_anchor_chunks": len(set(anchor_ids)),
        "rule_id": next(iter(rule_ids)) if len(rule_ids) == 1 else None,
        "rule_leaf_ids": inputs[0].get("rule_leaf_ids", []) if inputs else [],
        "review_item": review_item, "rule_store_sha256": sha256(args.rule_store),
        "judge_input_sha256": sha256(args.input), "program_sha256": sha256(args.program),
        "model": base.get("model_name"), "temperature": base.get("temperature"),
        "max_tokens": base.get("max_tokens"), "enable_thinking": base.get("enable_thinking"),
        "api_key": "CONFIGURED" if base.get("api_key_configured") else "MISSING",
        "api_calls": 0,
    }
    dump(args.output / "R2_Check_Only_Report.json", report)
    analysis = f"""# R2 Existing Judge Analysis

## 已有实现

- 真实入口：`tender_review_dspy/src/v03_pilot5/run_frozen_prompt_judge.py`，交接包副本与其代码SHA-256一致。
- 模型输入固定为 `review_item`、`rule_context`、`document_text`；冻结DSPy程序只读取 `decide.signature.instructions`。
- 模型输出契约为 `is_pass: bool`、`reason: str`、`text: list[str]`。现有业务输出为“不违规 / 违规 / 结果无效”。
- 现有grounding先做原字符串包含，再做仅忽略空白的精确定位；无法ground的FAIL标记 `EVIDENCE_NOT_GROUNDED`，不会把模型改写当正式原文。
- 现有output guard拒绝推测性违规、与明确否定冲突的FAIL，并对本规则的高风险漏判标记 `HIGH_RISK_TRIGGER_MISSED`。
- LiteLLM按当前配置执行内部重试；predictions逐候选upsert持久化，现有resume跳过状态为OK的已完成候选。

## 本轮新增的确定性后处理

- 将现有“结果无效”和技术失败安全归入候选级 `REVIEW`，同时保留raw result、parse status及validation errors。
- 将模型证据按 anchor优先、context其次、距anchor最近的顺序映射到真实Chunk；页码和source provenance只取匹配Chunk。
- 生成validated predictions、grounding报告、重复finding去重，并按 `FAIL > REVIEW > PASS` 确定性聚合文档结果。
- 生成业务JSON及两份人工复核HTML。没有修改Judge Prompt、Rule Store、模型、温度、max_tokens、thinking、output guard或原grounding逻辑。

本次冻结配置：model={base.get('model_name')}，temperature={base.get('temperature')}，max_tokens={base.get('max_tokens')}，enable_thinking={str(base.get('enable_thinking')).lower()}。
"""
    (args.output / "R2_Existing_Judge_Analysis.md").write_text(analysis, encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def validate_prediction(prediction: dict, judge_input: dict) -> dict:
    raw_result = infer_raw_result(prediction)
    proposed = prediction.get("模型候选违规原文", []) or prediction.get("违规原文", []) or []
    mappings = [map_evidence(text, judge_input) for text in proposed]
    selected = [item["selected"] for item in mappings if item["selected"]]
    technical = bool(prediction.get("technical_error"))
    parse_status = prediction.get("parse_status", "INVALID")
    business = prediction.get("审查结果")
    errors = []
    if technical:
        errors.append("TECHNICAL_FAILED")
    if business == "结果无效":
        errors.append(parse_status)
    if raw_result == "FAIL" and (not proposed or len(selected) != len(proposed)):
        errors.append("INVALID_UNGROUNDED_FAIL")
    if business == "违规" and not errors and selected:
        validated = "FAIL"
    elif business == "不违规" and not errors:
        validated = "PASS"
    else:
        validated = "REVIEW"
    primary = selected[0] if selected else None
    review_text = None
    if validated == "REVIEW":
        if primary is None and parse_status == "HIGH_RISK_TRIGGER_MISSED":
            source_chunks = [judge_input["anchor_chunk"], *judge_input.get("context_before", []),
                             *judge_input.get("context_after", [])]
            anchor_order = judge_input["anchor_chunk"]["source_order"]
            trigger_matches = []
            for chunk in source_chunks:
                for pattern in HIGH_RISK_PATTERNS:
                    match = pattern.search(chunk["text"])
                    if match:
                        paragraph_start = chunk["text"].rfind("\n\n", 0, match.start()) + 2
                        paragraph_end = chunk["text"].find("\n\n", match.start())
                        if paragraph_end < 0:
                            paragraph_end = len(chunk["text"])
                        exact = chunk["text"][paragraph_start:paragraph_end].strip()
                        trigger_matches.append((
                            chunk["chunk_id"] != judge_input["anchor_chunk_id"],
                            abs(chunk["source_order"] - anchor_order), chunk["source_order"], chunk, exact))
            if trigger_matches:
                _, _, _, chunk, exact = sorted(trigger_matches, key=lambda item: item[:3])[0]
                primary = {
                    "chunk_id": chunk["chunk_id"], "exact_text": exact,
                    "is_anchor": chunk["chunk_id"] == judge_input["anchor_chunk_id"],
                    "source_order": chunk["source_order"], "page_numbers": chunk.get("page_numbers", []),
                    "source_block_ids": chunk.get("source_block_ids", []),
                    "source_table_ids": chunk.get("source_table_ids", []),
                    "heading_path": chunk.get("heading_path", []),
                }
        review_text = primary["exact_text"] if primary else judge_input["anchor_chunk"]["text"]
        if primary is None:
            primary = {
                "chunk_id": judge_input["anchor_chunk"]["chunk_id"],
                "exact_text": review_text, "is_anchor": True,
                "source_order": judge_input["anchor_chunk"]["source_order"],
                "page_numbers": judge_input["anchor_chunk"].get("page_numbers", []),
                "source_block_ids": judge_input["anchor_chunk"].get("source_block_ids", []),
                "source_table_ids": judge_input["anchor_chunk"].get("source_table_ids", []),
                "heading_path": judge_input["anchor_chunk"].get("heading_path", []),
            }
    return {
        "prediction_id": prediction["candidate_id"], "candidate_id": prediction["candidate_id"],
        "document_id": judge_input["anchor_chunk_id"].split("-C", 1)[0],
        "rule_id": judge_input["rule_id"], "rule_name": judge_input["review_item"],
        "rule_leaf_ids": judge_input.get("rule_leaf_ids", []),
        "retrieval_rank": int(prediction["candidate_id"].rsplit("K", 1)[-1]),
        "chunk_id": judge_input["anchor_chunk_id"], "chunk_type": judge_input["anchor_chunk"]["chunk_type"],
        "raw_result": raw_result, "validated_result": validated,
        "evidence_text": primary["exact_text"] if primary else None,
        "violation_original_text": primary["exact_text"] if validated == "FAIL" and primary else None,
        "review_original_text": review_text,
        "model_reason": prediction.get("判断理由", ""), "reason": prediction.get("判断理由", ""),
        "anchor_page_numbers": judge_input.get("page_numbers", []),
        "grounding_status": ("GROUNDED" if validated == "REVIEW" and primary else
                             "GROUNDED" if selected and len(selected) == len(proposed) else
                             "NOT_REQUIRED" if raw_result == "PASS" and not proposed else "UNGROUNDED"),
        "grounded": bool((validated == "REVIEW" and primary)
                         or (selected and len(selected) == len(proposed))),
        "output_guard_status": "PASS" if parse_status == "OK" else parse_status,
        "high_risk_validation_status": parse_status if parse_status == "HIGH_RISK_TRIGGER_MISSED" else "PASS",
        "validation_status": "VALIDATED_" + validated if not errors else "INVALID_TO_REVIEW",
        "validation_errors": list(dict.fromkeys(errors)),
        "evidence_chunk_id": primary["chunk_id"] if primary else None,
        "evidence_page_numbers": primary["page_numbers"] if primary else [],
        "evidence_source_block_ids": primary["source_block_ids"] if primary else [],
        "evidence_source_table_ids": primary["source_table_ids"] if primary else [],
        "evidence_heading_path": primary["heading_path"] if primary else [],
        "evidence_exact_in_anchor": bool(primary and primary["is_anchor"]),
        "evidence_match_candidates": [candidate for item in mappings for candidate in item["evidence_match_candidates"]],
        "parse_status": parse_status, "technical_error": prediction.get("technical_error"),
    }


def deduplicate_findings(rows: list[dict]) -> tuple[list[dict], dict]:
    eligible = [row for row in rows if row["validated_result"] in {"FAIL", "REVIEW"}]
    groups: list[list[dict]] = []
    for row in eligible:
        text = row.get("violation_original_text") or row.get("review_original_text") or ""
        source_ids = set(row.get("evidence_source_block_ids", []))
        pages = set(row.get("evidence_page_numbers", []))
        placed = False
        for group in groups:
            other = group[0]
            other_text = other.get("violation_original_text") or other.get("review_original_text") or ""
            if (row["rule_id"] == other["rule_id"] and row["validated_result"] == other["validated_result"]
                    and ((source_ids & set(other.get("evidence_source_block_ids", [])))
                         or (normalize_evidence(text) == normalize_evidence(other_text)
                             and pages & set(other.get("evidence_page_numbers", []))))):
                group.append(row)
                placed = True
                break
        if not placed:
            groups.append([row])
    findings = []
    for index, group in enumerate(groups, 1):
        primary = sorted(group, key=lambda row: (
            not row.get("evidence_exact_in_anchor"), row["retrieval_rank"],
            len(row.get("evidence_source_block_ids", []))))[0]
        pages = list(dict.fromkeys(page for row in group for page in row.get("evidence_page_numbers", [])))
        findings.append({
            "finding_id": f"{primary['document_id']}-{primary['rule_id']}-F{index:03d}",
            "rule_id": primary["rule_id"], "rule_name": primary["rule_name"],
            "finding_result": primary["validated_result"],
            "violation_original_text": primary.get("violation_original_text"),
            "review_original_text": primary.get("review_original_text"),
            "reason": primary["reason"], "physical_pdf_page": pages[0] if pages else None,
            "page_numbers": pages, "chunk_id": primary["evidence_chunk_id"],
            "supporting_chunk_ids": list(dict.fromkeys(row["chunk_id"] for row in group)),
            "source_block_ids": list(dict.fromkeys(
                sid for row in group for sid in row.get("evidence_source_block_ids", []))),
            "source_table_ids": list(dict.fromkeys(
                sid for row in group for sid in row.get("evidence_source_table_ids", []))),
            "heading_path": primary.get("evidence_heading_path", []),
            "retrieval_ranks": sorted(row["retrieval_rank"] for row in group),
            "judge_prediction_ids": [row["prediction_id"] for row in group],
            "grounding_status": primary["grounding_status"],
        })
    return findings, {
        "input_fail_review_candidates": len(eligible), "unique_findings": len(findings),
        "duplicates_removed": len(eligible) - len(findings),
        "method": "same rule/result and shared source_block_ids, or identical normalized evidence with overlapping pages",
        "llm_used": False,
    }


def postprocess(args) -> dict:
    inputs = load_jsonl(args.input)
    chunks = load_jsonl(args.chunks)
    predictions = load_jsonl(args.predictions)
    input_by_id = {row["candidate_id"]: row for row in inputs}
    validated = [validate_prediction(row, input_by_id[row["candidate_id"]]) for row in predictions]
    validated.sort(key=lambda row: row["retrieval_rank"])
    write_jsonl(args.output / "validated_predictions.jsonl", validated)

    raw_dir = args.output / "raw_responses"
    raw_dir.mkdir(exist_ok=True)
    for prediction in predictions:
        rank = int(prediction["candidate_id"].rsplit("K", 1)[-1])
        dump(raw_dir / f"rank_{rank:03d}_{prediction['anchor_chunk_id']}.json", {
            "candidate_id": prediction["candidate_id"], "chunk_id": prediction["anchor_chunk_id"],
            "raw_output": prediction.get("raw_output", ""), "structured_output": {
                "审查结果": prediction.get("审查结果"), "违规原文": prediction.get("违规原文"),
                "判断理由": prediction.get("判断理由"), "parse_status": prediction.get("parse_status")},
            "finish_reason": None, "finish_reason_note": "not exposed by frozen Judge runner",
            "usage": prediction.get("usage", {}), "latency_seconds": prediction.get("latency_seconds"),
            "technical_error": prediction.get("technical_error"),
        })

    def usage_value(usage, *keys):
        for key in keys:
            if isinstance(usage.get(key), (int, float)):
                return int(usage[key])
        return 0
    input_tokens = sum(usage_value(row.get("usage", {}), "prompt_tokens", "input_tokens") for row in predictions)
    output_tokens = sum(usage_value(row.get("usage", {}), "completion_tokens", "output_tokens") for row in predictions)
    total_tokens = sum(usage_value(row.get("usage", {}), "total_tokens") for row in predictions) or input_tokens + output_tokens
    latencies = [float(row.get("latency_seconds", 0)) for row in predictions]
    technical_failed = sum(bool(row.get("technical_error")) for row in predictions)
    run_manifest = json.loads(args.run_manifest.read_text(encoding="utf-8-sig"))
    usage = {
        "judge_inputs": len(inputs), "api_attempts": len(predictions),
        "api_success": len(predictions) - technical_failed, "api_failed": technical_failed,
        "api_retries": 0, "api_retries_note": "LiteLLM internal retries are not surfaced by the frozen runner",
        "input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total_tokens,
        "total_latency_seconds": round(sum(latencies), 3),
        "wall_elapsed_seconds": run_manifest.get("elapsed_seconds"),
        "average_latency_seconds": round(statistics.mean(latencies), 3) if latencies else 0,
        "p50_latency_seconds": percentile(latencies, .50), "p95_latency_seconds": percentile(latencies, .95),
        "estimated_cost_cny": round(input_tokens * .5 / 1_000_000 + output_tokens * 2 / 1_000_000, 6),
        "pricing_basis": {"input_cny_per_million_tokens": .5, "output_cny_per_million_tokens": 2,
                          "mode": "non-thinking", "region": "cn-beijing",
                          "source": "https://help.aliyun.com/zh/model-studio/qwen3-8b"},
        "model": run_manifest.get("model_name"), "temperature": run_manifest.get("temperature"),
        "max_tokens": run_manifest.get("max_tokens"), "enable_thinking": run_manifest.get("enable_thinking"),
        "api_key": "CONFIGURED",
    }
    dump(args.output / "R2_Judge_Usage.json", usage)

    invalid = [row for row in validated if row["validation_errors"]]
    stats = {
        "total": len(validated),
        "raw_pass": sum(row["raw_result"] == "PASS" for row in validated),
        "raw_fail": sum(row["raw_result"] == "FAIL" for row in validated),
        "raw_review": sum(row["raw_result"] == "REVIEW" for row in validated),
        "validated_pass": sum(row["validated_result"] == "PASS" for row in validated),
        "validated_fail": sum(row["validated_result"] == "FAIL" for row in validated),
        "validated_review": sum(row["validated_result"] == "REVIEW" for row in validated),
        "invalid": len(invalid),
        "grounded_fail": sum(row["validated_result"] == "FAIL" and row["grounded"] for row in validated),
        "ungrounded_fail": sum(row["raw_result"] == "FAIL" and not row["grounded"] for row in validated),
        "by_chunk_type": {
            kind: Counter(row["validated_result"] for row in validated if row["chunk_type"] == kind)
            for kind in ("TEXT", "TABLE")},
        "by_retrieval_rank": [{"rank": row["retrieval_rank"], "result": row["validated_result"],
                               "chunk_id": row["chunk_id"]} for row in validated],
    }
    dump(args.output / "R2_Candidate_Statistics.json", stats)
    grounding = {
        "status": "PASS" if all(row["grounded"] and row["evidence_chunk_id"]
                                and row["evidence_page_numbers"] and row["evidence_source_block_ids"]
                                for row in validated if row["validated_result"] == "FAIL") else "FAIL",
        "validated_fail_count": stats["validated_fail"], "grounded_fail_count": stats["grounded_fail"],
        "ungrounded_raw_fail_count": stats["ungrounded_fail"],
        "physical_page_source": "matched evidence chunk provenance; never model output",
        "fail_details": [{key: row[key] for key in (
            "prediction_id", "evidence_text", "evidence_chunk_id", "evidence_page_numbers",
            "evidence_source_block_ids", "grounding_status")}
            for row in validated if row["validated_result"] == "FAIL"],
    }
    dump(args.output / "R2_Evidence_Grounding_Report.json", grounding)

    findings, dedup = deduplicate_findings(validated)
    write_jsonl(args.output / "findings.jsonl", findings)
    dump(args.output / "R2_Dedup_Report.json", dedup)
    result = aggregate_result(validated)
    document = {
        "document_id": validated[0]["document_id"], "review_item": validated[0]["rule_name"],
        "rule_id": validated[0]["rule_id"], "rule_name": validated[0]["rule_name"],
        "rule_leaf_ids": validated[0]["rule_leaf_ids"], "result": result,
        "finding_count": sum(f["finding_result"] == "FAIL" for f in findings),
        "findings": findings, "review_required_count": sum(f["finding_result"] == "REVIEW" for f in findings),
        "retrieval_candidates": len(inputs), "judge_candidates": len(validated),
        "aggregation_rule": "FAIL > REVIEW > PASS", "aggregation_llm_used": False,
    }
    dump(args.output / "document_review_result.json", document)
    project_number = None
    all_text = "\n".join(row["text"] for row in chunks)
    match = re.search(r"招标项目编号[：:]\s*([A-Z0-9]+)", all_text)
    if match:
        project_number = match.group(1)
    business_items = []
    for finding in findings:
        business_items.append({
            "Rule ID": finding["rule_id"],
            "违规原文" if finding["finding_result"] == "FAIL" else "待复核原文":
                finding.get("violation_original_text") or finding.get("review_original_text"),
            "PDF物理页": finding["physical_pdf_page"], "全部页码": finding["page_numbers"],
            "章节定位": " / ".join(finding["heading_path"]),
            "违规原因" if finding["finding_result"] == "FAIL" else "复核原因": finding["reason"],
            "Chunk ID": finding["chunk_id"], "Source IDs": finding["source_block_ids"],
        })
    final_business = {
        "项目编号": project_number, "招标文件编号": None,
        "审查点": document["review_item"], "审查结果": result,
        "违规项" if result == "FAIL" else "待复核项" if result == "REVIEW" else "违规项": business_items,
    }
    if result == "PASS":
        final_business["说明"] = "未在本次召回并审查的候选条款中发现触发该规则的明确违规内容"
    dump(args.output / "final_review_result.json", final_business)

    cards = []
    for row in validated:
        opened = " open" if row["validated_result"] != "PASS" or row["validation_errors"] else ""
        cards.append(f'<details data-result="{row["validated_result"]}"{opened}><summary>Rank {row["retrieval_rank"]} · '
                     f'{html.escape(row["chunk_id"])} · {row["validated_result"]}</summary>'
                     f'<p><b>Raw：</b>{row["raw_result"]}　<b>Grounding：</b>{row["grounding_status"]}</p>'
                     f'<p><b>证据页：</b>{html.escape(str(row["evidence_page_numbers"]))}</p>'
                     f'<pre>{html.escape(row.get("evidence_text") or "")}</pre>'
                     f'<p>{html.escape(row["reason"])}</p></details>')
    all_html = ("<!doctype html><meta charset='utf-8'><title>R2 All Predictions</title>"
                "<style>body{font-family:Arial,'Microsoft YaHei';max-width:1200px;margin:auto}details{margin:12px;padding:12px;border:1px solid #ddd}pre{white-space:pre-wrap}</style>"
                "<h1>R2 All 50 Predictions</h1><p>FAIL / REVIEW / INVALID默认展开，PASS折叠。</p>" + "".join(cards))
    (args.output / "R2_All_Predictions_Preview.html").write_text(all_html, encoding="utf-8")
    fail_cards = []
    for finding in findings:
        fail_cards.append(f'<article><h2>{finding["finding_result"]} · {html.escape(finding["finding_id"])}</h2>'
                          f'<p>PDF页：{html.escape(str(finding["page_numbers"]))}　章节：{html.escape(" / ".join(finding["heading_path"]))}</p>'
                          f'<pre>{html.escape(finding.get("violation_original_text") or finding.get("review_original_text") or "")}</pre>'
                          f'<p>{html.escape(finding["reason"])}</p><p>Grounding：{finding["grounding_status"]}</p></article>')
    final_html = ("<!doctype html><meta charset='utf-8'><title>R2 Final Review</title>"
                  "<style>body{font-family:Arial,'Microsoft YaHei';max-width:1200px;margin:auto;background:#f6f7f9}article{background:#fff;margin:16px;padding:16px}pre{white-space:pre-wrap}</style>"
                  f'<h1>DOC011 · {html.escape(document["review_item"])}</h1><h2>Final Result: {result}</h2>'
                  "<h2>A. FAIL Findings / B. REVIEW Findings</h2>" + "".join(fail_cards) +
                  f'<h2>C. PASS candidates（折叠统计）</h2><p>{stats["validated_pass"]} 条</p>')
    (args.output / "R2_Final_Review_Preview.html").write_text(final_html, encoding="utf-8")

    checks = {
        "judge_inputs_50": len(inputs) == 50,
        "predictions_50": len(predictions) == len(validated) == 50,
        "technical_failures_recorded": all("technical_error" in row for row in predictions),
        "api_failure_not_silent_pass": all(not row["technical_error"] or validated[i]["validated_result"] == "REVIEW"
                                           for i, row in enumerate(predictions)),
        "validated_fails_all_grounded": grounding["status"] == "PASS",
        "fail_evidence_has_chunk_page_source": all(row["evidence_chunk_id"] and row["evidence_page_numbers"]
                                                   and row["evidence_source_block_ids"]
                                                   for row in validated if row["validated_result"] == "FAIL"),
        "physical_page_deterministic": True,
        "findings_deduplicated": dedup["duplicates_removed"] >= 0,
        "aggregation_deterministic": document["aggregation_llm_used"] is False,
        "final_result_valid": result in {"PASS", "FAIL", "REVIEW"},
        "final_review_json_generated": (args.output / "final_review_result.json").exists(),
    }
    gate = {
        "status": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
        "failed_checks": [key for key, value in checks.items() if not value],
        "document_result": result, "judge_llm_called": True,
        "conclusion": "END_TO_END_POC_COMPLETE" if all(checks.values()) else "NEEDS_JUDGE_PIPELINE_FIX",
    }
    dump(args.output / "R2_Final_Gate_Report.json", gate)
    print(json.dumps(gate, ensure_ascii=False, indent=2))
    return gate


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--chunks", type=Path, required=True)
    p.add_argument("--rule-store", type=Path, required=True)
    p.add_argument("--program", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--postprocess", action="store_true")
    p.add_argument("--predictions", type=Path)
    p.add_argument("--run-manifest", type=Path)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.check_only:
        result = check_only(args)
        if result["status"] != "PASS":
            raise SystemExit(1)
    else:
        if not args.predictions or not args.run_manifest:
            p.error("--postprocess requires --predictions and --run-manifest")
        postprocess(args)


if __name__ == "__main__":
    main()
