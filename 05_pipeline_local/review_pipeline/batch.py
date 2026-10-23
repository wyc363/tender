from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from .config import load_config
from .orchestrator import PipelineOrchestrator
from .utils import write_json


SUMMARY_FIELDS = ["document_id", "filename", "physical_pages", "parser_status", "parser_vlm_calls",
                  "parser_tokens", "parser_cost_cny", "chunk_status", "total_chunks", "text_chunks",
                  "table_chunks", "retrieval_status", "retrieval_corpus", "top_k", "judge_status",
                  "judge_inputs", "raw_pass", "raw_fail", "raw_review", "validated_pass",
                  "validated_fail", "validated_review", "document_result", "total_duration_seconds",
                  "total_api_cost_cny", "pipeline_status", "error"]

MANUAL_FIELDS = ["document_id", "system_result", "human_result", "finding_id",
                 "system_finding_result", "violation_text_correct", "page_correct", "reason_correct",
                 "error_type", "human_comment"]


def write_batch_outputs(output_root: Path, rows: list[dict]) -> None:
    write_json(output_root / "I1_L_Batch_Summary.json", {"documents": rows,
               "total_documents": len(rows), "completed": sum(r.get("pipeline_status") == "COMPLETED" for r in rows),
               "failed": sum(r.get("pipeline_status") == "FAILED" for r in rows),
               "total_api_cost_cny": round(sum(float(r.get("total_api_cost_cny") or 0) for r in rows), 6)})
    with (output_root / "I1_L_Batch_Summary.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=SUMMARY_FIELDS, extrasaction="ignore"); writer.writeheader()
        for row in rows: writer.writerow(row)
    with (output_root / "I1_L_Manual_Evaluation.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=MANUAL_FIELDS); writer.writeheader()
        for row in rows:
            writer.writerow({"document_id": row.get("document_id"), "system_result": row.get("document_result"),
                             **{name: "" for name in MANUAL_FIELDS[2:]}})


def main() -> None:
    p = argparse.ArgumentParser(description="Sequential local PDF review batch")
    p.add_argument("--manifest", type=Path, required=True); p.add_argument("--rule-id", required=True)
    p.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "config" / "pipeline.local.yaml")
    p.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    args = p.parse_args(); config = load_config(args.config); entries = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    rows = []
    for entry in entries:
        try:
            rows.append(PipelineOrchestrator(config).run_document(entry["pdf"], entry.get("document_id"),
                        args.rule_id, resume=args.resume))
        except Exception as exc:
            rows.append({"document_id": entry.get("document_id"), "filename": Path(entry["pdf"]).name,
                         "pipeline_status": "FAILED", "error": str(exc)})
    write_batch_outputs(config.output_root, rows)
    print(json.dumps({"documents": len(rows), "completed": sum(r.get("pipeline_status") == "COMPLETED" for r in rows),
                      "failed": sum(r.get("pipeline_status") == "FAILED" for r in rows)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

