"""Run A2.1 boundary decisions with strict lossless validation."""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from .vlm_boundary_prompt import (PROMPT_VERSION, SCHEMA_VERSION, SYSTEM_PROMPT,
                                  active_blocks, build_user_prompt, project_block)
from .vlm_boundary_schema import boundary_schema_for, parse_json_response, validate_boundary
from .vlm_client import VLMClientResult, call_page_structure
from .vlm_config import ParserVLMSettings, estimate_cost
from .vlm_structure import read_jsonl, upsert_page_result, write_json, write_jsonl


def call_page_boundary(settings: ParserVLMSettings, image_path: Path,
                       prompt: str, active: list[dict[str, Any]]) -> VLMClientResult:
    return call_page_structure(settings, image_path, prompt, system_prompt=SYSTEM_PROMPT,
                               response_schema=boundary_schema_for(active),
                               schema_name="page_boundary")


def _image_index(manifest: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {int(item["page_no"]): item for item in manifest.get("images", [])}


def _empty_usage() -> dict[str, None]:
    return {"prompt_tokens": None, "input_tokens": None, "completion_tokens": None,
            "output_tokens": None, "total_tokens": None}


def _record(page_no: int, status: str, settings: ParserVLMSettings,
            active_ids: list[str], *, value: dict[str, Any] | None = None,
            latency: float = 0.0, usage: dict[str, Any] | None = None,
            errors: list[str] | None = None, mode: str | None = None,
            fallbacks: list[str] | None = None, finish_reason: str | None = None,
            api_called: bool = False) -> dict[str, Any]:
    value = value or {}
    return {"schema_version": SCHEMA_VERSION, "prompt_version": PROMPT_VERSION,
            "page_no": page_no, "status": status, "model": settings.model,
            "page_type": value.get("page_type"), "elements": value.get("elements", []),
            "active_block_ids": active_ids, "latency_seconds": latency,
            "usage": usage or _empty_usage(), "validation_errors": errors or [],
            "finish_reason": finish_reason, "structured_output_mode": mode,
            "compatibility_fallbacks": fallbacks or [], "api_called": api_called}


def _token_sum(rows: list[dict[str, Any]], key: str) -> int | None:
    called = [r for r in rows if r["api_called"]]
    values = [r["usage"].get(key) for r in called]
    return sum(values) if values and all(v is not None for v in values) else None


def build_usage_report(rows: list[dict[str, Any]], settings: ParserVLMSettings,
                       reused: int, called_this_run: int) -> dict[str, Any]:
    counts = Counter(r["status"] for r in rows); called = sum(r["api_called"] for r in rows)
    prompt = _token_sum(rows, "prompt_tokens"); completion = _token_sum(rows, "completion_tokens")
    total = _token_sum(rows, "total_tokens"); latency = sum(float(r["latency_seconds"]) for r in rows)
    return {"schema_version": SCHEMA_VERSION, "prompt_version": PROMPT_VERSION,
            "pages_requested": len(rows), "pages_selected": len(rows), "pages_called": called,
            "pages_called_this_run": called_this_run, "pages_reused_from_resume": reused,
            "pages_skipped": counts["SKIP"], "success": counts["SUCCESS"],
            "invalid": counts["INVALID"], "truncated": counts["INVALID_TRUNCATED"],
            "technical_failed": counts["TECHNICAL_FAILED"],
            "prompt_tokens": prompt, "input_tokens": prompt,
            "completion_tokens": completion, "output_tokens": completion, "total_tokens": total,
            "total_latency_seconds": latency,
            "average_latency_seconds_per_called_page": latency / called if called else None,
            "model": settings.model, "api_region": settings.api_region,
            "usage_complete": called == 0 or all(r["usage"].get("prompt_tokens") is not None and
                r["usage"].get("completion_tokens") is not None for r in rows if r["api_called"]),
            "cost": estimate_cost(settings, prompt, completion),
            "finish_reason_counts": dict(Counter(r["finish_reason"] for r in rows if r["finish_reason"])),
            "structured_output_modes": dict(Counter(r["structured_output_mode"] for r in rows if r["structured_output_mode"])),
            "compatibility_fallbacks": list(dict.fromkeys(x for r in rows for x in r["compatibility_fallbacks"]))}


def run_boundary_pages(page_records: list[dict[str, Any]], image_manifest: dict[str, Any],
                       pages: list[int], output_dir: Path, settings: ParserVLMSettings,
                       call_fn: Callable[..., VLMClientResult] = call_page_boundary,
                       resume: bool = False,
                       progress_fn: Callable[[dict[str, Any], bool], None] | None = None,
                       ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw_responses"; audit_dir = output_dir / "request_audit"
    raw_dir.mkdir(exist_ok=True); audit_dir.mkdir(exist_ok=True)
    records = {int(r["page_no"]): r for r in page_records}; images = _image_index(image_manifest)
    structures = output_dir / "page_structures.jsonl"
    existing = ({int(r["page_no"]): r for r in read_jsonl(structures)}
                if resume and structures.exists() else {})
    results = []; reused = called = 0
    for page_no in pages:
        page = records[page_no]; active = active_blocks(page)
        active_ids = [b["block_id"] for b in active]
        covered = {b["block_id"] for b in page.get("blocks", []) if "COVERED_BY_TABLE" in b.get("flags", [])}
        image = images.get(page_no)
        if not active:
            row = _record(page_no, "SKIP", settings, [], errors=["NO_ACTIVE_BLOCKS"])
            results.append(row); upsert_page_result(structures, row)
            if progress_fn: progress_fn(row, False)
            continue
        if not image or not Path(image["path"]).is_file():
            row = _record(page_no, "TECHNICAL_FAILED", settings, active_ids, errors=["PAGE_IMAGE_MISSING"])
            results.append(row); upsert_page_result(structures, row)
            if progress_fn: progress_fn(row, False)
            continue
        prompt = build_user_prompt(page_no, active)
        audit = {"schema_version": SCHEMA_VERSION, "prompt_version": PROMPT_VERSION,
                 "page_no": page_no, "model": settings.model, "api_base_host_region": settings.api_region,
                 "active_block_ids": active_ids, "covered_block_ids_excluded": sorted(covered),
                 "blocks_sent": [project_block(b) for b in active],
                 "parser_labels_for_posthoc_audit": {b["block_id"]: b.get("parser_label") for b in active},
                 "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest().upper(),
                 "prompt_characters": len(prompt),
                 "image": {k: image.get(k) for k in ("path", "width_px", "height_px", "bytes")},
                 "configured_max_pixels": settings.max_pixels, "api_key_in_audit": False}
        raw_path = raw_dir / f"page_{page_no:04d}.json"; audit_path = audit_dir / f"page_{page_no:04d}.json"
        prior = existing.get(page_no)
        if prior and prior.get("status") == "SUCCESS" and raw_path.is_file() and audit_path.is_file():
            old_audit = json.loads(audit_path.read_text(encoding="utf-8"))
            candidate = {"schema_version": prior.get("schema_version"), "page_no": prior.get("page_no"),
                         "page_type": prior.get("page_type"), "elements": prior.get("elements")}
            valid = not validate_boundary(candidate, page_no, active, covered, prior.get("finish_reason"))
            same = (prior.get("schema_version") == SCHEMA_VERSION and prior.get("prompt_version") == PROMPT_VERSION
                    and prior.get("model") == settings.model and prior.get("active_block_ids") == active_ids
                    and old_audit.get("prompt_sha256") == audit["prompt_sha256"])
            if valid and same:
                results.append(prior); reused += 1
                if progress_fn: progress_fn(prior, True)
                continue
        write_json(audit_path, audit); called += 1; started = time.perf_counter()
        try:
            response = call_fn(settings, Path(image["path"]), prompt, active)
            value, errors = parse_json_response(response.text); errors = list(errors)
            if value is not None:
                errors.extend(validate_boundary(value, page_no, active, covered, response.finish_reason))
            elif response.finish_reason == "length":
                errors.insert(0, "INVALID_TRUNCATED: finish_reason=length")
            status = "INVALID_TRUNCATED" if response.finish_reason == "length" else ("SUCCESS" if not errors else "INVALID")
            row = _record(page_no, status, settings, active_ids, value=value,
                          latency=response.latency_seconds, usage=response.usage, errors=errors,
                          mode=response.structured_output_mode, fallbacks=response.compatibility_fallbacks,
                          finish_reason=response.finish_reason, api_called=True)
            write_json(raw_path, {"schema_version": SCHEMA_VERSION, "prompt_version": PROMPT_VERSION,
                "page_no": page_no, "content": response.text, "response": response.raw_response,
                "finish_reason": response.finish_reason,
                "structured_output_mode": response.structured_output_mode,
                "max_pixels_forwarded": response.max_pixels_forwarded,
                "compatibility_fallbacks": response.compatibility_fallbacks})
        except Exception as exc:
            row = _record(page_no, "TECHNICAL_FAILED", settings, active_ids,
                          latency=time.perf_counter()-started,
                          errors=[f"API_ERROR: {type(exc).__name__}: {exc}"], api_called=True)
            write_json(raw_path, {"schema_version": SCHEMA_VERSION, "prompt_version": PROMPT_VERSION,
                                  "page_no": page_no, "error_type": type(exc).__name__, "error": str(exc)})
        results.append(row); upsert_page_result(structures, row)
        if progress_fn: progress_fn(row, False)
    write_jsonl(structures, results)
    usage = build_usage_report(results, settings, reused, called)
    write_json(output_dir / "vlm_usage_report.json", usage)
    return results, usage
