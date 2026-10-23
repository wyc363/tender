"""Build requests, run fixed-page VLM calls, validate, and write A2 artifacts."""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from .vlm_client import VLMClientResult, call_page_structure
from .vlm_config import ParserVLMSettings, estimate_cost
from .vlm_prompt import active_blocks, build_user_prompt, project_block
from .vlm_schema import parse_json_response, validate_page_structure


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def upsert_page_result(path: Path, row: dict[str, Any]) -> None:
    """Persist one page atomically so interruption never loses completed calls."""
    rows = read_jsonl(path) if path.exists() else []
    by_page = {int(item["page_no"]): item for item in rows}
    by_page[int(row["page_no"])] = row
    temp = path.with_suffix(path.suffix + ".tmp")
    write_jsonl(temp, [by_page[page] for page in sorted(by_page)])
    temp.replace(path)


def parse_pages(value: str, total_pages: int) -> list[int]:
    try:
        pages = [int(token.strip()) for token in value.split(",") if token.strip()]
    except ValueError as exc:
        raise ValueError("--pages must be comma-separated physical page numbers") from exc
    if not pages:
        raise ValueError("--run requires at least one page in --pages")
    if len(pages) != len(set(pages)):
        raise ValueError("--pages must not contain duplicates")
    invalid = [page for page in pages if not 1 <= page <= total_pages]
    if invalid:
        raise ValueError(f"--pages outside 1..{total_pages}: {invalid}")
    if len(pages) > 20:
        raise ValueError("Phase A2 permits at most 20 manually selected pages per run")
    return pages


def _image_index(image_manifest: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {int(item["page_no"]): item for item in image_manifest.get("images", [])}


def _empty_usage() -> dict[str, None]:
    return {"prompt_tokens": None, "input_tokens": None,
            "completion_tokens": None, "output_tokens": None, "total_tokens": None}


def _record(page_no: int, status: str, settings: ParserVLMSettings,
            active_ids: list[str], latency: float = 0.0,
            usage: dict[str, Any] | None = None, errors: list[str] | None = None,
            value: dict[str, Any] | None = None,
            mode: str | None = None, fallbacks: list[str] | None = None,
            api_called: bool = False) -> dict[str, Any]:
    value = value or {}
    return {
        "page_no": page_no, "status": status, "model": settings.model,
        "reading_order": value.get("reading_order", []),
        "classifications": value.get("classifications", []),
        "page_type": value.get("page_type"), "confidence": value.get("confidence"),
        "active_block_ids": active_ids, "latency_seconds": latency,
        "usage": usage or _empty_usage(), "validation_errors": errors or [],
        "structured_output_mode": mode,
        "compatibility_fallbacks": fallbacks or [],
        "api_called": api_called,
    }


def run_structure_pages(page_records: list[dict[str, Any]], image_manifest: dict[str, Any],
                        pages: list[int], output_dir: Path, settings: ParserVLMSettings,
                        call_fn: Callable[..., VLMClientResult] = call_page_structure,
                        resume: bool = False,
                        progress_fn: Callable[[dict[str, Any], bool], None] | None = None,
                        finalize_a2: bool = True,
                        ) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir, audit_dir = output_dir / "raw_responses", output_dir / "request_audit"
    raw_dir.mkdir(exist_ok=True)
    audit_dir.mkdir(exist_ok=True)
    records_by_page = {int(record["page_no"]): record for record in page_records}
    images_by_page = _image_index(image_manifest)
    structures_path = output_dir / "page_structures.jsonl"
    existing_by_page = ({int(row["page_no"]): row for row in read_jsonl(structures_path)}
                        if resume and structures_path.exists() else {})
    results: list[dict[str, Any]] = []
    reused_count = called_this_run = 0

    for page_no in pages:
        page = records_by_page[page_no]
        active = active_blocks(page)
        active_ids = [block["block_id"] for block in active]
        covered_ids = {block["block_id"] for block in page.get("blocks", [])
                       if "COVERED_BY_TABLE" in block.get("flags", [])}
        image = images_by_page.get(page_no)
        if not active:
            result = _record(page_no, "SKIP", settings, [], errors=["NO_ACTIVE_BLOCKS"])
            results.append(result); upsert_page_result(structures_path, result)
            if progress_fn: progress_fn(result, False)
            continue
        if not image or not Path(image["path"]).is_file():
            result = _record(page_no, "TECHNICAL_FAILED", settings, active_ids,
                             errors=["PAGE_IMAGE_MISSING"])
            results.append(result); upsert_page_result(structures_path, result)
            if progress_fn: progress_fn(result, False)
            continue

        prompt = build_user_prompt(page_no, active)
        request_audit = {
            "page_no": page_no, "model": settings.model,
            "api_base_host_region": settings.api_region,
            "active_block_ids": active_ids, "covered_block_ids_excluded": sorted(covered_ids),
            "blocks_sent": [project_block(block) for block in active],
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest().upper(),
            "prompt_characters": len(prompt),
            "image": {key: image.get(key) for key in
                      ("path", "width_px", "height_px", "bytes")},
            "actual_image_pixels": ((image.get("width_px") or 0) *
                                    (image.get("height_px") or 0)),
            "configured_max_pixels": settings.max_pixels,
            "api_key_in_audit": False,
        }
        prior = existing_by_page.get(page_no)
        prior_audit_path = audit_dir / f"page_{page_no:04d}.json"
        prior_raw_path = raw_dir / f"page_{page_no:04d}.json"
        if prior and prior.get("status") == "SUCCESS" and prior_audit_path.is_file() and prior_raw_path.is_file():
            prior_audit = json.loads(prior_audit_path.read_text(encoding="utf-8"))
            prior_value = {key: prior.get(key) for key in
                           ("page_no", "reading_order", "classifications", "page_type", "confidence")}
            valid = not validate_page_structure(prior_value, page_no, active, covered_ids)
            same_request = (prior.get("model") == settings.model and
                            prior.get("active_block_ids") == active_ids and
                            prior_audit.get("prompt_sha256") == request_audit["prompt_sha256"])
            if valid and same_request:
                results.append(prior); reused_count += 1
                if progress_fn: progress_fn(prior, True)
                continue
        write_json(audit_dir / f"page_{page_no:04d}.json", request_audit)
        started = time.perf_counter()
        called_this_run += 1
        try:
            response = call_fn(settings, Path(image["path"]), prompt)
            value, parse_errors = parse_json_response(response.text)
            errors = list(parse_errors)
            if value is not None:
                errors.extend(validate_page_structure(value, page_no, active, covered_ids))
            status = "SUCCESS" if not errors else "INVALID"
            result = _record(page_no, status, settings, active_ids,
                             response.latency_seconds, response.usage, errors,
                             value, response.structured_output_mode,
                             response.compatibility_fallbacks, api_called=True)
            write_json(raw_dir / f"page_{page_no:04d}.json", {
                "page_no": page_no, "content": response.text,
                "response": response.raw_response,
                "structured_output_mode": response.structured_output_mode,
                "max_pixels_forwarded": response.max_pixels_forwarded,
                "compatibility_fallbacks": response.compatibility_fallbacks,
            })
        except Exception as exc:
            latency = time.perf_counter() - started
            result = _record(page_no, "TECHNICAL_FAILED", settings, active_ids, latency,
                             errors=[f"API_ERROR: {type(exc).__name__}: {exc}"], api_called=True)
            write_json(raw_dir / f"page_{page_no:04d}.json", {
                "page_no": page_no, "error_type": type(exc).__name__, "error": str(exc)
            })
        results.append(result)
        upsert_page_result(structures_path, result)
        if progress_fn: progress_fn(result, False)

    write_jsonl(structures_path, results)
    usage_report = build_usage_report(results, settings, reused_count, called_this_run)
    write_json(output_dir / "vlm_usage_report.json", usage_report)
    check_report = build_a2_check_report(results, pages, output_dir, settings)
    if finalize_a2:
        write_json(output_dir / "a2_check_report.json", check_report)
        write_acceptance_markdown(output_dir / "A2验收说明.md", results, usage_report, check_report)
    return results, usage_report, check_report


def _complete_token_sum(rows: list[dict[str, Any]], key: str) -> int | None:
    called = [row for row in rows if row["api_called"]]
    values = [row["usage"].get(key) for row in called]
    return sum(values) if values and all(value is not None for value in values) else None


def build_usage_report(rows: list[dict[str, Any]], settings: ParserVLMSettings,
                       reused_count: int = 0, called_this_run: int | None = None) -> dict[str, Any]:
    counts = Counter(row["status"] for row in rows)
    prompt = _complete_token_sum(rows, "prompt_tokens")
    completion = _complete_token_sum(rows, "completion_tokens")
    total = _complete_token_sum(rows, "total_tokens")
    called = sum(row["api_called"] for row in rows)
    called_this_run = called if called_this_run is None else called_this_run
    total_latency = sum(float(row["latency_seconds"]) for row in rows)
    return {
        "pages_requested": len(rows), "pages_selected": len(rows), "pages_called": called,
        "pages_called_this_run": called_this_run,
        "pages_reused_from_resume": reused_count,
        "pages_skipped": counts["SKIP"], "success": counts["SUCCESS"],
        "invalid": counts["INVALID"], "technical_failed": counts["TECHNICAL_FAILED"],
        "prompt_tokens": prompt, "input_tokens": prompt,
        "completion_tokens": completion, "output_tokens": completion,
        "total_tokens": total,
        "total_latency_seconds": total_latency,
        "average_latency_seconds_per_called_page": total_latency / called if called else None,
        "model": settings.model, "api_region": settings.api_region,
        "usage_complete": called == 0 or all(row["usage"].get("prompt_tokens") is not None and
                                             row["usage"].get("completion_tokens") is not None
                                             for row in rows if row["api_called"]),
        "cost": estimate_cost(settings, prompt, completion),
        "structured_output_modes": dict(Counter(row["structured_output_mode"] for row in rows
                                                 if row["structured_output_mode"])),
        "compatibility_fallbacks": list(dict.fromkeys(
            fallback for row in rows for fallback in row["compatibility_fallbacks"])),
    }


def build_a2_check_report(rows: list[dict[str, Any]], pages: list[int], output_dir: Path,
                          settings: ParserVLMSettings) -> dict[str, Any]:
    called_pages = [row["page_no"] for row in rows if row["api_called"]]
    checks = {
        "one_result_per_requested_page": len(rows) == len(pages) and
                                         [row["page_no"] for row in rows] == pages,
        "request_audit_for_every_called_page": all(
            (output_dir / "request_audit" / f"page_{page:04d}.json").is_file()
            for page in called_pages),
        "raw_response_for_every_called_page": all(
            (output_dir / "raw_responses" / f"page_{page:04d}.json").is_file()
            for page in called_pages),
        "all_success_or_explicit_failure": all(row["status"] in
            {"SUCCESS", "INVALID", "TECHNICAL_FAILED", "SKIP"} for row in rows),
        "no_renderer_or_downstream_output": not any(
            (output_dir / name).exists() for name in ("good.md", "source_map.jsonl", "chunks.jsonl")),
    }
    statuses = Counter(row["status"] for row in rows)
    return {
        "status": "PASS" if all(checks.values()) and not statuses["INVALID"] and
                               not statuses["TECHNICAL_FAILED"] else "COMPLETE_WITH_ISSUES",
        "phase": "A2", "requested_pages": pages, "model": settings.model,
        "checks": checks, "status_counts": dict(statuses),
        "parser_vlm_config": settings.public_dict(),
        "scope_exclusions": ["Good Markdown", "Chunker", "Retrieval/Judge",
                             "cross-page relations", "Renderer"],
    }


def write_acceptance_markdown(path: Path, rows: list[dict[str, Any]], usage: dict[str, Any],
                              check: dict[str, Any]) -> None:
    lines = ["# Phase A2 验收说明", "", f"- 状态：{check['status']}",
             f"- 模型：{usage['model']}", f"- 请求页：{usage['pages_requested']}",
             f"- 实际调用：{usage['pages_called']}", f"- 成功：{usage['success']}",
             f"- INVALID：{usage['invalid']}", f"- 技术失败：{usage['technical_failed']}",
             f"- Structured Output：{usage['structured_output_modes']}",
             f"- 兼容 fallback：{usage['compatibility_fallbacks'] or '无'}", "",
             "## 逐页结构结果", ""]
    for row in rows:
        lines.extend([f"### PDF 第 {row['page_no']} 页", "",
                      f"状态：`{row['status']}`；page_type：`{row['page_type']}`；"
                      f"confidence：`{row['confidence']}`。", ""])
        class_by_id = {item.get("block_id"): item for item in row["classifications"]}
        if row["reading_order"]:
            for index, block_id in enumerate(row["reading_order"], 1):
                item = class_by_id.get(block_id, {})
                level = f" level={item.get('heading_level')}" if item.get("type") == "heading" else ""
                lines.append(f"{index}. `{block_id}` → `{item.get('type')}`{level}")
        else:
            lines.append("无有效 reading_order。")
        if row["validation_errors"]:
            lines.extend(["", "校验错误："] +
                         [f"- `{error}`" for error in row["validation_errors"]])
        lines.append("")
    lines.extend(["## Usage 与费用", "", "```json",
                  json.dumps(usage, ensure_ascii=False, indent=2), "```", ""])
    path.write_text("\n".join(lines), encoding="utf-8")
