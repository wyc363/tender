from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_config
from .orchestrator import PipelineError, PipelineOrchestrator


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Local PDF-to-review pipeline")
    p.add_argument("--pdf", type=Path, required=True)
    p.add_argument("--document-id")
    p.add_argument("--rule-id", required=True)
    p.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "config" / "pipeline.local.yaml")
    p.add_argument("--output-root", type=Path)
    p.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--stop-after", choices=("input", "docling", "parser", "chunk", "retrieval", "judge"))
    p.add_argument("--regression-profile", choices=("DOC011",))
    p.add_argument("--check-only", action="store_true")
    return p


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    orchestrator = PipelineOrchestrator(load_config(args.config))
    if args.check_only:
        result = orchestrator.check_only(args.pdf, args.rule_id, args.document_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result["status"] == "PASS" else 1)
    try:
        result = orchestrator.run_document(args.pdf, args.document_id, args.rule_id, args.output_root,
                                           args.resume, args.stop_after, args.regression_profile)
    except (PipelineError, FileNotFoundError, ValueError) as exc:
        print(json.dumps({"pipeline": "FAILED", "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    if result.get("pipeline_status") == "COMPLETED":
        doc = result["document_id"]
        output = orchestrator.config.output_root / doc / "final_review_result.json"
        report = orchestrator.config.output_root / doc / "final_review_report.html"
        client_content = orchestrator.config.output_root / doc / "client_content_result.json"
        client_response = orchestrator.config.output_root / doc / "client_api_response.json"
        print("=================================")
        print(f"Document: {doc}")
        print("Pipeline: COMPLETED")
        print(f"Final Result: {result['document_result']}")
        print(f"Findings: {result['findings']}")
        print(f"Review Items: {result['review_items']}")
        print(f"Duration: {result['total_duration_seconds']}")
        print(f"Estimated Cost: ¥{result['total_api_cost_cny']}")
        print(f"Output:\n{output}")
        print(f"HTML Report:\n{report}")
        print(f"Client Content JSON:\n{client_content}")
        print(f"Client API Response JSON:\n{client_response}")
        print("=================================")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
