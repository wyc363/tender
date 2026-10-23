from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .client_api_adapter import build_client_api_outputs
from .config import load_config
from .gates import judge_generic_gate
from .report import build_combined_report
from .utils import read_json, read_jsonl, write_json


POSTPROCESS_FILES = (
    "validated_predictions.jsonl", "findings.jsonl", "document_review_result.json",
    "final_review_result.json", "R2_Judge_Usage.json", "R2_Candidate_Statistics.json",
    "R2_Evidence_Grounding_Report.json", "R2_Dedup_Report.json", "R2_Final_Gate_Report.json",
    "R2_All_Predictions_Preview.html", "R2_Final_Review_Preview.html",
)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    temp.replace(path)


def _review_candidate_ids(validated: list[dict[str, Any]]) -> list[str]:
    return [str(row["candidate_id"]) for row in validated
            if str(row.get("validated_result") or "").upper() == "REVIEW"]


def _merge_predictions(existing: list[dict[str, Any]], replacements: list[dict[str, Any]],
                       selected_ids: list[str]) -> list[dict[str, Any]]:
    replacement_by_id = {str(row.get("candidate_id")): row for row in replacements}
    if set(replacement_by_id) != set(selected_ids):
        raise RuntimeError(f"Retry output IDs do not match selected REVIEW IDs: expected={selected_ids}, actual={sorted(replacement_by_id)}")
    existing_ids = {str(row.get("candidate_id")) for row in existing}
    missing = sorted(set(selected_ids) - existing_ids)
    if missing:
        raise RuntimeError(f"Selected REVIEW IDs are missing from existing predictions: {missing}")
    return [replacement_by_id.get(str(row.get("candidate_id")), row) for row in existing]


def _run(command: list[str], cwd: Path, environment: dict[str, str] | None = None) -> None:
    process_environment = os.environ.copy()
    if environment:
        process_environment.update(environment)
    completed = subprocess.run(command, cwd=str(cwd), check=False, env=process_environment)
    if completed.returncode:
        raise RuntimeError(f"Command failed with exit code {completed.returncode}: {' '.join(command)}")


def _archive(root: Path, timestamp: str) -> Path:
    archive = root / "selective_retry_archive" / timestamp
    archive.mkdir(parents=True, exist_ok=False)
    shutil.copytree(root / "05_judge", archive / "05_judge")
    for name in ("final_review_result.json", "review_point_result.json",
                 "client_aggregation_audit.json", "client_content_result.json",
                 "client_api_response.json", "final_review_report.html", "pipeline_summary.json"):
        source = root / name
        if source.is_file():
            shutil.copy2(source, archive / name)
    return archive


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Rerun only REVIEW candidates and safely merge them into all Judge outputs")
    parser.add_argument("--document-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=project / "config" / "pipeline.local.yaml")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--enrich-fail", action="store_true",
                        help="Call enrichment only if the merged document result is FAIL")
    args = parser.parse_args()

    root = args.document_root.expanduser().resolve()
    config = load_config(args.config)
    judge, retrieval = root / "05_judge", root / "04_retrieval"
    chunks = root / "03_chunk" / "chunks.jsonl"
    validated_path, predictions_path = judge / "validated_predictions.jsonl", judge / "predictions.jsonl"
    judge_inputs_path = retrieval / "judge_inputs.jsonl"
    for required in (validated_path, predictions_path, judge_inputs_path, chunks):
        if not required.is_file():
            raise FileNotFoundError(required)

    validated = read_jsonl(validated_path)
    selected_ids = _review_candidate_ids(validated)
    all_inputs = read_jsonl(judge_inputs_path)
    input_by_id = {str(row.get("candidate_id")): row for row in all_inputs}
    selected_inputs = [input_by_id[candidate_id] for candidate_id in selected_ids if candidate_id in input_by_id]
    missing_inputs = sorted(set(selected_ids) - set(input_by_id))
    check = {
        "status": "PASS" if selected_ids and not missing_inputs else "NO_REVIEW" if not selected_ids else "FAIL",
        "document_root": str(root), "review_candidates": len(selected_ids), "candidate_ids": selected_ids,
        "missing_inputs": missing_inputs, "full_judge_candidates": len(all_inputs),
        "api_calls_planned": len(selected_ids) if not missing_inputs else 0,
    }
    write_json(root / "selective_review_retry_check.json", check)
    print(json.dumps(check, ensure_ascii=False, indent=2), flush=True)
    if args.check_only or not selected_ids:
        return
    if missing_inputs:
        raise RuntimeError(f"REVIEW candidate inputs missing: {missing_inputs}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive = _archive(root, timestamp)
    work = root / "selective_retry_work" / timestamp
    retry_output, postprocess_output = work / "judge_retry", work / "postprocessed"
    retry_input = work / "review_judge_inputs.jsonl"
    retry_output.mkdir(parents=True, exist_ok=True)
    postprocess_output.mkdir(parents=True, exist_ok=True)
    _write_jsonl(retry_input, selected_inputs)

    print(f"仅重跑 {len(selected_ids)} 条 REVIEW：{', '.join(selected_ids)}", flush=True)
    _run([str(config.judge_python), "-m", "src.v03_pilot5.run_frozen_prompt_judge",
          "--input", str(retry_input), "--program", str(config.prompt_program_path),
          "--output", str(retry_output), "--run", "--no-resume"], config.judge_project,
         config.model_environment("judge"))

    replacements = read_jsonl(retry_output / "predictions.jsonl")
    existing = read_jsonl(predictions_path)
    merged = _merge_predictions(existing, replacements, selected_ids)
    merged_path = work / "merged_predictions.jsonl"
    _write_jsonl(merged_path, merged)

    print("正在基于合并后的完整候选集执行本地校验与聚合……", flush=True)
    _run([str(config.retrieval_python), str(config.retrieval_project / "run_r2_pipeline.py"),
          "--input", str(judge_inputs_path), "--chunks", str(chunks),
          "--rule-store", str(config.rule_store_path), "--program", str(config.prompt_program_path),
          "--output", str(postprocess_output), "--postprocess", "--predictions", str(merged_path),
          "--run-manifest", str(retry_output / "run_manifest.json")], config.retrieval_project)

    # Existing outputs are changed only after retry and full postprocessing succeed.
    _write_jsonl(predictions_path, merged)
    for name in POSTPROCESS_FILES:
        source = postprocess_output / name
        if source.is_file():
            shutil.copy2(source, judge / name)
    raw_source, raw_target = postprocess_output / "raw_responses", judge / "raw_responses"
    if raw_source.is_dir():
        raw_target.mkdir(exist_ok=True)
        for source in raw_source.iterdir():
            if source.is_file():
                shutil.copy2(source, raw_target / source.name)
    generic_gate = judge_generic_gate(judge, len(all_inputs))
    write_json(judge / "Judge_Generic_Gate.json", generic_gate)
    if generic_gate.get("status") != "PASS":
        raise RuntimeError(f"Merged Judge generic gate failed: {generic_gate}")
    shutil.copy2(judge / "final_review_result.json", root / "final_review_result.json")
    content_path, response_path = build_client_api_outputs(
        root, judge_env_file=config.judge_env_file, enrich_fail=args.enrich_fail,
        pass_evidence_top_n=config.client_pass_evidence_top_n,
        model_environment=config.model_environment("judge"),
        enrichment_settings=config.client_settings)
    report_path = build_combined_report(root)

    new_validated = {row["candidate_id"]: row for row in read_jsonl(judge / "validated_predictions.jsonl")}
    old_by_id = {row["candidate_id"]: row for row in validated}
    comparison = [{
        "candidate_id": candidate_id,
        "before_raw_result": old_by_id[candidate_id].get("raw_result"),
        "before_validated_result": old_by_id[candidate_id].get("validated_result"),
        "after_raw_result": new_validated[candidate_id].get("raw_result"),
        "after_validated_result": new_validated[candidate_id].get("validated_result"),
        "after_validation_errors": new_validated[candidate_id].get("validation_errors") or [],
    } for candidate_id in selected_ids]
    retry_manifest = read_json(retry_output / "run_manifest.json")
    def usage_value(row: dict[str, Any], *names: str) -> int:
        usage = row.get("usage") or {}
        for name in names:
            if isinstance(usage.get(name), (int, float)):
                return int(usage[name])
        return 0
    retry_input_tokens = sum(usage_value(row, "prompt_tokens", "input_tokens") for row in replacements)
    retry_output_tokens = sum(usage_value(row, "completion_tokens", "output_tokens") for row in replacements)
    retry_total_tokens = sum(usage_value(row, "total_tokens") for row in replacements) or retry_input_tokens + retry_output_tokens
    retry_cost_cny = round(retry_input_tokens * .5 / 1_000_000 + retry_output_tokens * 2 / 1_000_000, 6)
    summary_path = root / "pipeline_summary.json"
    if summary_path.is_file():
        summary = read_json(summary_path)
        summary["raw_pass"] = read_json(judge / "R2_Candidate_Statistics.json").get("raw_pass")
        summary["raw_fail"] = read_json(judge / "R2_Candidate_Statistics.json").get("raw_fail")
        summary["raw_review"] = read_json(judge / "R2_Candidate_Statistics.json").get("raw_review")
        summary["validated_pass"] = read_json(judge / "R2_Candidate_Statistics.json").get("validated_pass")
        summary["validated_fail"] = read_json(judge / "R2_Candidate_Statistics.json").get("validated_fail")
        summary["validated_review"] = read_json(judge / "R2_Candidate_Statistics.json").get("validated_review")
        summary["document_result"] = read_json(judge / "document_review_result.json").get("result")
        summary["selective_retry_api_calls"] = int(summary.get("selective_retry_api_calls") or 0) + len(selected_ids)
        summary["selective_retry_tokens"] = int(summary.get("selective_retry_tokens") or 0) + retry_total_tokens
        summary["selective_retry_cost_cny"] = round(float(summary.get("selective_retry_cost_cny") or 0) + retry_cost_cny, 6)
        summary["total_api_cost_cny"] = round(float(summary.get("total_api_cost_cny") or 0) + retry_cost_cny, 6)
        write_json(summary_path, summary)
    audit = {
        "status": "COMPLETE", "mode": "SELECTIVE_REVIEW_RETRY", "archive": str(archive),
        "work_dir": str(work), "full_candidates": len(all_inputs),
        "api_candidates_retried": len(selected_ids), "api_candidate_ids": selected_ids,
        "api_calls": retry_manifest.get("rows_processed_this_run"),
        "technical_failures": retry_manifest.get("technical_failures_this_run"),
        "retry_elapsed_seconds": retry_manifest.get("elapsed_seconds"),
        "retry_usage": {"input_tokens": retry_input_tokens, "output_tokens": retry_output_tokens,
                        "total_tokens": retry_total_tokens, "estimated_cost_cny": retry_cost_cny},
        "comparison": comparison,
        "merged_statistics": read_json(judge / "R2_Candidate_Statistics.json"),
        "document_result": read_json(judge / "document_review_result.json").get("result"),
        "client_content_result": str(content_path), "client_api_response": str(response_path),
        "final_review_report": str(report_path),
    }
    write_json(root / "selective_review_retry_report.json", audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
