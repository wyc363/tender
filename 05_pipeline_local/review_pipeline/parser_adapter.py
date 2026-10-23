from __future__ import annotations

import hashlib
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any


@contextmanager
def _scoped_environment(values: dict[str, str] | None):
    values = values or {}
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run_parser(pdf: Path, document_json: Path, a1_dir: Path, output: Path,
               parser_project: Path, env_file: Path, resume: bool,
               environment: dict[str, str] | None = None) -> dict[str, Any]:
    sys.path.insert(0, str(parser_project))
    from docling_postprocess.deterministic_structure import active_blocks
    from docling_postprocess.goodmd_renderer import chunk_preflight, render_goodmd
    from docling_postprocess.pf2_pipeline import code_fingerprint, run_full
    from docling_postprocess.vlm_cli import _validate_a1_inputs
    from docling_postprocess.vlm_config import estimate_cost, load_parser_vlm_settings, validate_parser_vlm_settings
    from docling_postprocess.vlm_structure import read_jsonl, write_json

    _validate_a1_inputs(a1_dir, pdf, document_json)
    with _scoped_environment(environment):
        settings = load_parser_vlm_settings(env_file)
    validate_parser_vlm_settings(settings, require_api=True)
    records = read_jsonl(a1_dir / "page_blocks.jsonl")
    manifest = json.loads((a1_dir / "page_images" / "manifest.json").read_text(encoding="utf-8"))
    fp = code_fingerprint()
    pdf_sha = hashlib.sha256(pdf.read_bytes()).hexdigest().upper()
    json_sha = hashlib.sha256(document_json.read_bytes()).hexdigest().upper()
    input_fp = hashlib.sha256((pdf_sha + json_sha + fp["combined_sha256"] + settings.model
                              + str(settings.max_pixels)).encode()).hexdigest().upper()
    finals, routes, calls, reused = run_full(records, manifest, output, settings, input_fp, resume)
    expected = [b["block_id"] for p in records for b in active_blocks(p)]
    covered = {b["block_id"] for p in records for b in p.get("blocks", [])
               if "COVERED_BY_TABLE" in b.get("flags", [])}
    actual = [sid for page in finals for element in page["ordered_elements"] for sid in element["source_ids"]]
    source_tables = [b["block_id"] for p in records for b in active_blocks(p) if b["kind"] == "table"]
    missing = sorted(set(expected) - set(actual)); duplicate = sorted(s for s in set(actual) if actual.count(s) > 1)
    unknown = sorted(set(actual) - set(expected)); leaks = sorted(set(actual) & covered)
    failed_calls = [call for call in calls if call.get("status") != "SUCCESS"]
    fallback_ok = all(any(r.get(key) in {"FALLBACK_A1", "SKIPPED_NO_ELIGIBLE_SOURCE_FACTS"}
                          for key in ("vlm_order_status", "vlm_table_status"))
                      for r in routes if r.get("fallback_used"))
    automatic = {
        "physical_pages_positive": len(records) > 0,
        "active_pages_positive": len(finals) > 0,
        "source_coverage_100_percent": len(set(actual) & set(expected)) == len(set(expected)),
        "missing_source_ids_zero": not missing, "duplicate_source_ids_zero": not duplicate,
        "unknown_source_ids_zero": not unknown, "covered_block_leaks_zero": not leaks,
        "all_source_tables_traceable": set(source_tables) <= set(actual),
        "invalid_vlm_repairs_not_adopted": all(
            page.get("order_repair_status") in {"SUCCESS", "FALLBACK_A1", "NOT_CALLED"}
            and page.get("table_repair_status") in {
                "SUCCESS", "FALLBACK_A1", "NOT_CALLED", "SKIPPED_NO_ELIGIBLE_SOURCE_FACTS"
            }
            for page in finals
        ),
        "vlm_failures_have_safe_fallback": fallback_ok,
        "no_technical_fatal": True,
    }
    output.mkdir(parents=True, exist_ok=True)
    if all(automatic.values()):
        _, validation = render_goodmd(records, finals, output)
        preflight = chunk_preflight((output / "good.md").read_text(encoding="utf-8"), finals)
    else:
        validation = {"status": "NOT_RUN"}; preflight = {"ready_for_chunker": False}
    automatic.update({
        "goodmd_validation_pass": validation.get("status") == "PASS",
        "render_source_coverage_100_percent": validation.get("render_source_coverage") == 1,
        "source_map_valid": (output / "source_map.jsonl").is_file() and
            bool(read_jsonl(output / "source_map.jsonl")),
        "chunk_preflight_ready": bool(preflight.get("ready_for_chunker")),
    })
    prompt_values = [c.get("usage", {}).get("prompt_tokens") for c in calls]
    completion_values = [c.get("usage", {}).get("completion_tokens") for c in calls]
    prompt = sum(prompt_values) if all(v is not None for v in prompt_values) else None
    completion = sum(completion_values) if all(v is not None for v in completion_values) else None
    usage = {"vlm_attempts": len(calls), "api_success": len(calls) - len(failed_calls),
             "api_failed": len(failed_calls), "input_tokens": prompt, "output_tokens": completion,
             "total_tokens": prompt + completion if prompt is not None and completion is not None else None,
             "latency_seconds": sum(float(c.get("latency_seconds", 0)) for c in calls),
             "estimated_cost": estimate_cost(settings, prompt, completion), "reused_pages": reused,
             "api_key": "CONFIGURED"}
    report = {"status": "PASS" if all(automatic.values()) else "FAIL", "checks": automatic,
              "physical_pages": len(records), "active_pages": len(finals),
              "source_active_blocks": len(expected), "source_coverage": len(set(actual) & set(expected)) / len(set(expected)),
              "missing_source_ids": missing, "duplicate_source_ids": duplicate,
              "unknown_source_ids": unknown, "covered_block_leaks": leaks, **usage,
              "input_fingerprint": input_fp, "generic_gate": True}
    write_json(output / "Parser_Generic_Gate.json", report)
    write_json(output / "PF2_VLM_Usage.json", usage)
    write_json(output / "PF2_Chunk_Preflight.json", preflight)
    return report


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
