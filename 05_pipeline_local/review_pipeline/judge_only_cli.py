from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from .config import load_config
from .orchestrator import PipelineOrchestrator
from .utils import read_json, read_jsonl, sha256_file, write_json


REUSED_STAGE_DIRS = ("00_input", "01_docling", "02_parser", "03_chunk", "04_retrieval")


def _same_file(left: Path, right: Path) -> bool:
    return left.is_file() and right.is_file() and sha256_file(left) == sha256_file(right)


def _prepare_reused_stages(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name in REUSED_STAGE_DIRS:
        old = source / name
        new = target / name
        if not old.is_dir():
            raise FileNotFoundError(old)
        if not new.exists():
            shutil.copytree(old, new)
    # These two files are the compatibility boundary for Judge-only reuse.
    for relative in (Path("03_chunk/chunks.jsonl"), Path("04_retrieval/judge_inputs.jsonl")):
        if not _same_file(source / relative, target / relative):
            raise RuntimeError(f"Reused stage mismatch after copy: {relative}")
    for name in ("pipeline_manifest.json", "pipeline_summary.json"):
        old = source / name
        new = target / name
        if old.is_file() and not new.exists():
            shutil.copy2(old, new)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Reuse parser/chunk/retrieval artifacts and rerun all Judge candidates")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--document-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=project / "config" / "pipeline.local.yaml")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--enrich-fail", action="store_true")
    args = parser.parse_args()

    source = args.source_root.expanduser().resolve()
    target = args.document_root.expanduser().resolve()
    chunks = source / "03_chunk" / "chunks.jsonl"
    judge_inputs = source / "04_retrieval" / "judge_inputs.jsonl"
    candidates = read_jsonl(judge_inputs) if judge_inputs.is_file() else []
    check = {
        "status": "PASS" if chunks.is_file() and len(candidates) > 0 else "FAIL",
        "source_root": str(source), "document_root": str(target),
        "chunks_exists": chunks.is_file(), "judge_inputs_exists": judge_inputs.is_file(),
        "judge_candidates": len(candidates), "candidate_ids_unique": len({r.get('candidate_id') for r in candidates}) == len(candidates),
        "parser_calls_planned": 0, "retrieval_calls_planned": 0, "judge_calls_planned": len(candidates),
    }
    target.mkdir(parents=True, exist_ok=True)
    write_json(target / "judge_only_check.json", check)
    print(json.dumps(check, ensure_ascii=False, indent=2), flush=True)
    if args.check_only:
        return
    if check["status"] != "PASS" or not check["candidate_ids_unique"]:
        raise RuntimeError(f"Judge-only preflight failed: {check}")

    _prepare_reused_stages(source, target)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive = target / "judge_only_archive" / timestamp
    archive.mkdir(parents=True, exist_ok=False)
    old_judge = target / "05_judge"
    if old_judge.is_dir():
        shutil.copytree(old_judge, archive / "05_judge")
    for name in ("final_review_result.json", "review_point_result.json",
                 "client_aggregation_audit.json", "client_content_result.json",
                 "client_api_response.json", "final_review_report.html", "pipeline_summary.json"):
        old = target / name
        if old.is_file():
            shutil.copy2(old, archive / name)

    config = load_config(args.config)
    orchestrator = PipelineOrchestrator(config)
    orchestrator._log_path = target / "pipeline.log"
    work_judge = target / "judge_only_work" / timestamp / "05_judge"
    work_judge.mkdir(parents=True, exist_ok=True)
    print(f"复用解析、Chunk和召回，重新审查全部 {len(candidates)} 条候选……", flush=True)
    artifacts, metrics, gate = orchestrator._judge_stage(
        target / "03_chunk" / "chunks.jsonl",
        target / "04_retrieval" / "judge_inputs.jsonl",
        work_judge,
    )
    if gate.get("status") != "PASS":
        raise RuntimeError(f"Judge gate failed: {gate}")

    target_judge = target / "05_judge"
    target_judge.mkdir(parents=True, exist_ok=True)
    for item in work_judge.iterdir():
        destination = target_judge / item.name
        if item.is_dir():
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(item, destination)
        else:
            shutil.copy2(item, destination)
    # Finalize with current interface adapter and HTML renderer.
    orchestrator._finalize_stage({"root": target, "judge": target_judge})

    summary_path = target / "pipeline_summary.json"
    summary = read_json(summary_path) if summary_path.is_file() else {}
    stats = read_json(target_judge / "R2_Candidate_Statistics.json")
    document = read_json(target_judge / "document_review_result.json")
    usage = read_json(target_judge / "R2_Judge_Usage.json")
    summary.update({
        "document_id": summary.get("document_id") or target.name,
        "judge_status": "PASS", "judge_inputs": len(candidates),
        "raw_pass": stats.get("raw_pass"), "raw_fail": stats.get("raw_fail"), "raw_review": stats.get("raw_review"),
        "validated_pass": stats.get("validated_pass"), "validated_fail": stats.get("validated_fail"),
        "validated_review": stats.get("validated_review"), "document_result": document.get("result"),
        "judge_only_reused_parser": True, "judge_only_reused_retrieval": True,
        "judge_only_api_calls": len(candidates), "judge_tokens": usage.get("total_tokens"),
        "judge_cost_cny": usage.get("estimated_cost_cny"), "pipeline_status": "COMPLETED",
        "client_content_result": str(target / "client_content_result.json"),
        "client_api_response": str(target / "client_api_response.json"),
    })
    write_json(summary_path, summary)
    report = {
        "status": "COMPLETE", "mode": "JUDGE_ONLY_REUSE_PREJUDGE", "source_root": str(source),
        "document_root": str(target), "archive": str(archive), "work_judge": str(work_judge),
        "parser_calls": 0, "retrieval_calls": 0, "judge_calls": len(candidates),
        "judge_metrics": metrics, "document_result": document.get("result"),
        "final_review_report": str(target / "final_review_report.html"),
    }
    write_json(target / "judge_only_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
