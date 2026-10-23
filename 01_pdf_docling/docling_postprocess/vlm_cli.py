"""Offline Phase A/A1 entry point: page blocks, page images and checks only."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from .furniture import Config, repair
from .loader import Document
from .page_blocks import build_page_records, write_page_blocks
from .page_image import inspect_pdf, render_page_images, sha256_file
from .vlm_config import load_parser_vlm_settings, validate_parser_vlm_settings
from .vlm_selector_report import generate_selector_reports
from .vlm_selector_structure import run_selector_pages
from .vlm_prompt import active_blocks
from .vlm_structure import parse_pages, read_jsonl, run_structure_pages, write_json


def _normalized_name(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).lower()


def _output_writable(output_dir: Path) -> bool:
    output_dir.mkdir(parents=True, exist_ok=True)
    marker = output_dir / f".write_check_{os.getpid()}"
    try:
        marker.write_text("ok", encoding="ascii")
        marker.unlink()
        return True
    except OSError:
        return False


def _size_checks(doc: Document, pdf_info: dict[str, Any]) -> dict[str, Any]:
    comparisons = []
    for pdf_page in pdf_info["pages"]:
        page_no = pdf_page["page_no"]
        json_size = doc.data.get("pages", {}).get(str(page_no), {}).get("size", {})
        jw, jh = json_size.get("width"), json_size.get("height")
        if not jw or not jh:
            comparisons.append({"page_no": page_no, "match": False, "reason": "missing_json_size"})
            continue
        width_error = abs(float(jw) - pdf_page["width"]) / max(float(jw), 1.0)
        height_error = abs(float(jh) - pdf_page["height"]) / max(float(jh), 1.0)
        comparisons.append({"page_no": page_no, "match": width_error <= 0.02 and height_error <= 0.02,
                            "width_relative_error": width_error,
                            "height_relative_error": height_error})
    return {"all_page_sizes_match": all(item["match"] for item in comparisons),
            "mismatches": [item for item in comparisons if not item["match"]]}


def run_check_only(pdf_path: Path, json_path: Path, output_dir: Path,
                   vlm_mode: str = "all", image_width: int = 1400,
                   jpeg_quality: int = 85, max_pixels: int = 1_572_864,
                   force_render: bool = False) -> dict[str, Any]:
    pdf_path, json_path, output_dir = pdf_path.resolve(), json_path.resolve(), output_dir.resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    if not json_path.is_file():
        raise FileNotFoundError(f"Docling JSON not found: {json_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a PDF file: {pdf_path}")

    writable = _output_writable(output_dir)
    doc = Document.load(json_path)
    if not isinstance(doc.data.get("body"), dict) or "texts" not in doc.data:
        raise ValueError(f"Unsupported Docling document: {json_path}")

    decisions, suspicious = repair(doc, Config())
    records, blocks_report = build_page_records(doc, decisions, suspicious)
    page_blocks_path = output_dir / "page_blocks.jsonl"
    write_page_blocks(page_blocks_path, records)
    furniture_path = output_dir / "furniture_decisions.json"
    furniture_path.write_text(json.dumps({
        "document": doc.data.get("name"), "decisions": decisions,
        "suspicious_candidates": suspicious,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    pdf_info = inspect_pdf(pdf_path)
    size_result = _size_checks(doc, pdf_info)
    image_manifest = render_page_images(pdf_path, output_dir / "page_images",
                                        width_px=image_width, quality=jpeg_quality,
                                        force=force_render)
    json_pages = len(doc.data.get("pages", {}))
    pdf_pages = pdf_info["page_count"]
    name_matches = _normalized_name(str(doc.data.get("name", ""))) == _normalized_name(pdf_path.stem)
    active_pages = [record for record in records if any(
        "COVERED_BY_TABLE" not in block.get("flags", []) for block in record["blocks"])]
    expected_calls = (len(active_pages) if vlm_mode == "all" else sum(
        any(block["kind"] == "table" and "COVERED_BY_TABLE" not in block.get("flags", [])
            for block in record["blocks"]) for record in records))
    parser_settings = load_parser_vlm_settings()
    validate_parser_vlm_settings(parser_settings, require_api=False)
    essential_checks = {
        "output_directory_writable": writable,
        "pdf_json_page_count_match": pdf_pages == json_pages,
        "pdf_json_page_sizes_match": size_result["all_page_sizes_match"],
        "page_blocks_page_count_match": len(records) == pdf_pages,
        **blocks_report["checks"],
        "page_image_count_match": image_manifest["page_count"] == pdf_pages,
        "api_not_called": True,
    }
    report = {
        "status": "PASS" if all(essential_checks.values()) else "FAIL",
        "phase": "A/A1",
        "execution_mode": "CHECK_ONLY_OFFLINE",
        "api_calls_executed": 0,
        "inputs": {
            "pdf": str(pdf_path), "pdf_sha256": sha256_file(pdf_path),
            "document_json": str(json_path), "document_json_sha256": sha256_file(json_path),
            "docling_document_name": doc.data.get("name"),
        },
        "correspondence": {
            "pdf_pages": pdf_pages, "json_pages": json_pages,
            "file_stem_matches_document_name": name_matches,
            "page_size_check": size_result,
            "note": "Name mismatch is informational; page count and per-page dimensions are the hard checks.",
        },
        "configuration": {
            "vlm_mode": vlm_mode, "image_width_px": image_width,
            "jpeg_quality": jpeg_quality, "future_api_max_pixels_per_image": max_pixels,
            "parser_vlm": parser_settings.public_dict(),
            "parser_vlm_api_ready": parser_settings.api_configured,
        },
        "future_api_estimate": {
            "calls": expected_calls,
            "raw_image_pixels": image_manifest["total_pixels"],
            "max_pixels_after_api_cap": expected_calls * max_pixels,
            "basis": ("one request per page having at least one active block"
                      if vlm_mode == "all" else
                      "preliminary A2 table-page estimate; formal auto routing is deferred"),
        },
        "artifacts": {
            "page_blocks": str(page_blocks_path),
            "page_images": str(output_dir / "page_images"),
            "page_image_manifest": str(output_dir / "page_images" / "manifest.json"),
            "furniture_decisions": str(furniture_path),
        },
        "page_blocks_summary": blocks_report,
        "page_images_summary": {key: image_manifest[key] for key in
                                ("page_count", "rendered_this_run", "reused_from_cache", "total_pixels")},
        "checks": essential_checks,
        "warnings": (["PDF filename and Docling document name differ; verify the pair manually."]
                     if not name_matches else []) + doc.warnings,
        "scope": {
            "implemented": ["page_blocks", "table-covered dedup flags", "page_images", "check-only"],
            "deferred": ["qwen3.7-flash execution", "VLM structure output",
                         "cross-page relations", "VLM Markdown renderer"],
        },
    }
    report_path = output_dir / "a1_check_report.json"
    report["artifacts"]["check_report"] = str(report_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _validate_a1_inputs(a1_dir: Path, pdf_path: Path, json_path: Path) -> None:
    report_path = a1_dir / "a1_check_report.json"
    if not report_path.is_file():
        raise FileNotFoundError(f"A1 check report not found: {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "PASS" or not all(report.get("checks", {}).values()):
        raise RuntimeError("A2 requires a passing A1 check report")
    inputs = report.get("inputs", {})
    if inputs.get("pdf_sha256", "").upper() != sha256_file(pdf_path):
        raise RuntimeError("A1 page images do not belong to the requested PDF")
    if inputs.get("document_json_sha256", "").upper() != sha256_file(json_path):
        raise RuntimeError("A1 page_blocks do not belong to the requested Docling JSON")


def run_a2(pdf_path: Path, json_path: Path, a1_dir: Path, output_dir: Path,
           pages_value: str | None, env_file: Path | None = None,
           all_active_pages: bool = False, resume: bool = True,
           baseline_a2_dir: Path | None = None) -> dict[str, Any]:
    pdf_path, json_path = pdf_path.resolve(), json_path.resolve()
    a1_dir, output_dir = a1_dir.resolve(), output_dir.resolve()
    _validate_a1_inputs(a1_dir, pdf_path, json_path)
    settings = load_parser_vlm_settings(env_file)
    validate_parser_vlm_settings(settings, require_api=True)
    page_records = read_jsonl(a1_dir / "page_blocks.jsonl")
    image_manifest = json.loads((a1_dir / "page_images" / "manifest.json").read_text(encoding="utf-8"))
    pages = ([record["page_no"] for record in page_records if active_blocks(record)]
             if all_active_pages else parse_pages(pages_value or "", len(page_records)))
    total = len(pages)
    completed = 0
    def progress(row: dict[str, Any], reused: bool) -> None:
        nonlocal completed
        completed += 1
        marker = "REUSED" if reused else row["status"]
        print(f"[{completed}/{total}] page {row['page_no']:04d} {marker}", flush=True)
    if settings.max_tokens != 2500:
        raise ValueError("Phase A2.2 requires PARSER_VLM_MAX_TOKENS=2500")
    rows, usage = run_selector_pages(page_records, image_manifest, pages, output_dir, settings,
                                     resume=resume, progress_fn=progress)
    usage["pages_skipped"] = len(page_records) - len(pages) if all_active_pages else 0
    cost = usage.get("cost", {})
    usage["estimated_input_cost_cny"] = cost.get("input_cost")
    usage["estimated_output_cost_cny"] = cost.get("output_cost")
    usage["estimated_total_cost_cny"] = cost.get("estimated_cost")
    write_json(output_dir / "vlm_usage_report.json", usage)
    old_full = output_dir.parent / "vlm_poc_doc011_a2_full" / "page_structures.jsonl"
    if not old_full.is_file():
        raise FileNotFoundError(f"A2-Full regression source not found: {old_full}")
    a21_path = output_dir.parent / "vlm_poc_doc011_a21_20pages" / "page_structures.jsonl"
    if not a21_path.is_file():
        raise FileNotFoundError(f"A2.1 regression source not found: {a21_path}")
    reports = generate_selector_reports(page_records, rows, pages, output_dir, a1_dir,
                                        old_full, a21_path, settings)
    check = reports["check"]
    phase = "A2.2"
    result = {"status": check["status"], "phase": phase, "api_calls": usage["pages_called"],
            "pages_called_this_run": usage["pages_called_this_run"],
            "pages_reused_from_resume": usage["pages_reused_from_resume"],
            "requested_pages": pages, "output_dir": str(output_dir),
            "usage_report": str(output_dir / "vlm_usage_report.json"),
            "page_structures": str(output_dir / "page_structures.jsonl"),
            "check_report": str(output_dir / "a2_2_check_report.json"),
            "preview": str(output_dir / "A2_2_模拟GoodMD.html")}
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase A/A1 offline preparation: page_blocks + page images + checks."
    )
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--a1-dir", type=Path,
                        help="Passing A1 output whose page blocks and cached JPEGs are reused by --run.")
    page_selection = parser.add_mutually_exclusive_group()
    page_selection.add_argument("--pages", help="Comma-separated physical PDF pages for a bounded A2 run.")
    page_selection.add_argument("--all-active-pages", action="store_true",
                                help="A2-Full: select every page having at least one active block.")
    parser.add_argument("--baseline-a2-dir", type=Path,
                        help="Fixed five-page A2 output used for regression audit.")
    parser.add_argument("--no-resume", action="store_true",
                        help="Disable the default reuse of locally validated SUCCESS pages.")
    parser.add_argument("--env-file", type=Path,
                        help="Optional Parser-only .env; defaults to 01_pdf_docling/.env.")
    parser.add_argument("--vlm-mode", choices=("all", "auto"), default="all",
                        help="Used only to estimate future calls; no API is called.")
    parser.add_argument("--image-width", type=int, default=1400)
    parser.add_argument("--jpeg-quality", type=int, default=85)
    parser.add_argument("--max-pixels", type=int, default=1_572_864)
    parser.add_argument("--force-render", action="store_true")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-only", action="store_true",
                      help="Generate/check A1 artifacts and make zero API calls.")
    mode.add_argument("--run", action="store_true",
                      help="Run Phase A2 for explicitly selected pages.")
    args = parser.parse_args()
    if args.check_only:
        report = run_check_only(args.pdf, args.json, args.output_dir, args.vlm_mode,
                                args.image_width, args.jpeg_quality, args.max_pixels,
                                args.force_render)
        summary = {
            "status": report["status"], "phase": report["phase"],
            "execution_mode": report["execution_mode"],
            "api_calls_executed": report["api_calls_executed"],
            "future_api_calls_estimated": report["future_api_estimate"]["calls"],
            "artifacts": report["artifacts"], "checks": report["checks"],
        }
    else:
        if args.a1_dir is None:
            parser.error("--run requires --a1-dir so A2 reuses the accepted A1 artifacts")
        if not args.pages and not args.all_active_pages:
            parser.error("--run requires either --pages or --all-active-pages")
        try:
            summary = run_a2(args.pdf, args.json, args.a1_dir, args.output_dir,
                             args.pages, args.env_file, args.all_active_pages,
                             not args.no_resume, args.baseline_a2_dir)
        except (FileNotFoundError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
    print(json.dumps({
        **summary
    }, ensure_ascii=False, indent=2))
    if summary["status"] not in {"PASS", "COMPLETE_WITH_ISSUES"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
