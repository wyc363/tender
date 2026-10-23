"""Deterministic A2-Full statistics, risk screening and technical gates."""

from __future__ import annotations

import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .vlm_config import ParserVLMSettings
from .vlm_prompt import active_blocks
from .vlm_schema import validate_page_structure
from .vlm_structure import read_jsonl, write_json


BASELINE_PAGES = [5, 12, 17, 38, 167]
NUMBERED_CLAUSE = re.compile(r"^\s*\d+(?:\.\d+){1,3}\.?\s*")


def _classes(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["block_id"]: item for item in row.get("classifications", [])}


def _docling_expected(block: dict[str, Any]) -> str:
    if block["kind"] == "table":
        return "table"
    return "heading" if block.get("parser_label") == "section_header" else "paragraph"


def _confidence_distribution(rows: list[dict[str, Any]]) -> dict[str, int]:
    result = Counter()
    for row in rows:
        value = row.get("confidence")
        if not isinstance(value, (int, float)):
            result["unavailable"] += 1
        elif value < .8:
            result["lt_0.80"] += 1
        elif value < .9:
            result["0.80_to_0.899"] += 1
        elif value < .95:
            result["0.90_to_0.949"] += 1
        elif value < .99:
            result["0.95_to_0.989"] += 1
        else:
            result["gte_0.99"] += 1
    return dict(result)


def find_over_heading(page: dict[str, Any], row: dict[str, Any]) -> list[dict[str, Any]]:
    by_id = {block["block_id"]: block for block in active_blocks(page)}
    class_by_id = _classes(row)
    numbered_types = [item["type"] for block_id, item in class_by_id.items()
                      if block_id in by_id and NUMBERED_CLAUSE.match(by_id[block_id].get("text", ""))]
    paragraph_majority = numbered_types.count("paragraph") > numbered_types.count("heading")
    candidates = []
    for block_id, item in class_by_id.items():
        block = by_id.get(block_id)
        if not block or item.get("type") != "heading" or block["kind"] != "text":
            continue
        text = " ".join(str(block.get("text", "")).split())
        reasons = []
        if NUMBERED_CLAUSE.match(text): reasons.append("numbered_clause_prefix")
        if "：" in text and len(text.split("：", 1)[1].strip()) >= 4: reasons.append("colon_with_clause_content")
        if len(text) >= 80: reasons.append("long_complete_statement")
        if text.rstrip().endswith(("；", "。", ";", ".")): reasons.append("prose_ending_punctuation")
        if paragraph_majority and NUMBERED_CLAUSE.match(text): reasons.append("numbered_siblings_mostly_paragraph")
        if reasons:
            candidates.append({"page_no": row["page_no"], "block_id": block_id,
                "text_preview": text[:240], "vlm_type": "heading",
                "parser_label": block.get("parser_label"), "risk_reason": reasons})
    return candidates


def build_statistics(page_records: list[dict[str, Any]], rows: list[dict[str, Any]],
                     pdf_pages: int, active_pages: list[int]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    pages = {record["page_no"]: record for record in page_records}
    statuses = Counter(row["status"] for row in rows)
    types, levels = Counter(), Counter()
    order_changed = type_changed_pages = 0
    over_heading = []
    schema_modes = Counter()
    max_pixel_fallback = 0
    for row in rows:
        page = pages[row["page_no"]]
        if row["status"] == "SUCCESS":
            initial = [block["block_id"] for block in active_blocks(page)]
            order_changed += row.get("reading_order") != initial
            class_by_id = _classes(row)
            changed = any(class_by_id.get(block["block_id"], {}).get("type") != _docling_expected(block)
                          for block in active_blocks(page))
            type_changed_pages += changed
            for item in row.get("classifications", []):
                types[item.get("type")] += 1
                if item.get("type") == "heading": levels[item.get("heading_level")] += 1
            over_heading.extend(find_over_heading(page, row))
        if row.get("structured_output_mode"): schema_modes[row["structured_output_mode"]] += 1
        max_pixel_fallback += "max_pixels_not_forwarded_after_compatibility_error" in row.get("compatibility_fallbacks", [])
    total_active = sum(len(active_blocks(pages[page])) for page in active_pages)
    stats = {
        "pdf_pages": pdf_pages, "active_pages": len(active_pages),
        "success_pages": statuses["SUCCESS"], "invalid_pages": statuses["INVALID"],
        "technical_failed_pages": statuses["TECHNICAL_FAILED"],
        "total_active_blocks": total_active,
        "heading_blocks": types["heading"], "paragraph_blocks": types["paragraph"],
        "table_blocks": types["table"], "ignore_blocks": types["ignore"],
        "heading_level_1": levels[1], "heading_level_2": levels[2],
        "pages_with_reading_order_change": order_changed,
        "pages_without_reading_order_change": statuses["SUCCESS"] - order_changed,
        "pages_not_comparable_due_to_invalid_or_failure": len(rows) - statuses["SUCCESS"],
        "pages_with_type_change_vs_docling": type_changed_pages,
        "json_schema_strict_pages": schema_modes["json_schema_strict"],
        "json_object_fallback_pages": schema_modes["json_object_local_strict_validation"],
        "max_pixels_fallback_pages": max_pixel_fallback,
        "confidence_distribution": _confidence_distribution(rows),
        "suspect_over_heading_blocks": len(over_heading),
        "note": "Docling differences and SUSPECT_OVER_HEADING are audit signals, not automatic errors."
    }
    return stats, over_heading


def build_risk_pages(page_records: list[dict[str, Any]], rows: list[dict[str, Any]],
                     over_heading: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pages = {record["page_no"]: record for record in page_records}
    over_by_page = defaultdict(list)
    for item in over_heading: over_by_page[item["page_no"]].append(item)
    risks = []
    for row in rows:
        page = pages[row["page_no"]]; blocks = active_blocks(page); classes = _classes(row)
        reasons = []
        if row["status"] != "SUCCESS": reasons.append("status_not_success")
        if isinstance(row.get("confidence"), (int, float)) and row["confidence"] < .9: reasons.append("confidence_below_0.90")
        if row.get("structured_output_mode") != "json_schema_strict": reasons.append("structured_output_fallback")
        if "max_pixels_not_forwarded_after_compatibility_error" in row.get("compatibility_fallbacks", []): reasons.append("max_pixels_fallback")
        headings = 0
        if row["status"] == "SUCCESS":
            initial = [block["block_id"] for block in blocks]
            if row.get("reading_order") != initial: reasons.append("reading_order_changed")
            if any(item.get("type") == "ignore" for item in row.get("classifications", [])): reasons.append("contains_ignore")
            if over_by_page[row["page_no"]]: reasons.append("SUSPECT_OVER_HEADING")
            headings = sum(item.get("type") == "heading" for item in row.get("classifications", []))
            if headings >= 8: reasons.append("heading_count_abnormally_high")
            if len(blocks) >= 4 and headings / len(blocks) >= .5: reasons.append("heading_ratio_abnormally_high")
            has_table = any(block["kind"] == "table" for block in blocks)
            non_table_changes = sum(block["kind"] != "table" and
                classes.get(block["block_id"], {}).get("type") != _docling_expected(block) for block in blocks)
            if has_table and non_table_changes >= 2: reasons.append("table_page_surrounding_text_type_changes")
            section_blocks = [block for block in blocks if block.get("parser_label") == "section_header"]
            section_conflicts = sum(classes.get(block["block_id"], {}).get("type") != "heading"
                                    for block in section_blocks)
            if section_conflicts >= 3 or (len(section_blocks) >= 2 and section_conflicts / len(section_blocks) >= .5):
                reasons.append("many_docling_section_header_conflicts")
        if reasons:
            risks.append({"page_no": row["page_no"], "status": row["status"],
                "confidence": row.get("confidence"), "risk_reasons": reasons,
                "active_blocks": len(blocks), "heading_blocks": headings,
                "suspect_over_heading": over_by_page[row["page_no"]]})
    return risks


def build_manual_suggestions(page_records: list[dict[str, Any]], rows: list[dict[str, Any]],
                             risks: list[dict[str, Any]], target: int = 25) -> list[dict[str, Any]]:
    pages = {record["page_no"]: record for record in page_records}
    row_by_page = {row["page_no"]: row for row in rows}
    reasons = defaultdict(list)
    def add(page: int, reason: str):
        if reason not in reasons[page]: reasons[page].append(reason)
    for page in BASELINE_PAGES:
        if page in row_by_page: add(page, "fixed_A2_baseline")
    for risk in risks:
        for reason in risk["risk_reasons"]: add(risk["page_no"], reason)
    for row in sorted(rows, key=lambda item: item.get("confidence") if isinstance(item.get("confidence"), (int, float)) else -1)[:5]:
        add(row["page_no"], "lowest_confidence")
    changed = [row for row in rows if row["status"] == "SUCCESS" and row.get("reading_order") !=
               [block["block_id"] for block in active_blocks(pages[row["page_no"]])]]
    for row in changed: add(row["page_no"], "reading_order_changed")
    table_pages = sorted((row for row in rows if any(b["kind"] == "table" for b in active_blocks(pages[row["page_no"]]))),
                         key=lambda row: len(active_blocks(pages[row["page_no"]])), reverse=True)
    for row in table_pages[:5]: add(row["page_no"], "complex_table_page")
    ordinary = [row["page_no"] for row in rows if row["page_no"] not in reasons and
                not any(b["kind"] == "table" for b in active_blocks(pages[row["page_no"]]))]
    for page in random.Random(42).sample(ordinary, min(5, len(ordinary))): add(page, "random_ordinary_negative_sample")
    for row in sorted(rows, key=lambda item: item["page_no"]):
        if len(reasons) >= target: break
        add(row["page_no"], "coverage_fill")
    priority = {"status_not_success": 0, "structured_output_fallback": 1,
                "max_pixels_fallback": 1, "lowest_confidence": 2,
                "SUSPECT_OVER_HEADING": 3, "reading_order_changed": 4,
                "complex_table_page": 5, "fixed_A2_baseline": 6,
                "random_ordinary_negative_sample": 7, "coverage_fill": 8}
    mandatory = {page for page, page_reasons in reasons.items() if page in BASELINE_PAGES or
                 any(reason in {"status_not_success", "structured_output_fallback", "max_pixels_fallback"}
                     for reason in page_reasons)}
    ranked = sorted((page for page in reasons if page not in mandatory),
                    key=lambda page: (min(priority.get(r, 5) for r in reasons[page]), page))
    selected = sorted(mandatory, key=lambda page: (min(priority.get(r, 5) for r in reasons[page]), page))
    selected.extend(ranked[:max(0, max(target, min(30, len(mandatory))) - len(selected))])
    return [{"page_no": page, "priority_reasons": reasons[page],
             "status": row_by_page[page]["status"], "confidence": row_by_page[page].get("confidence")}
            for page in selected]


def build_baseline_regression(rows: list[dict[str, Any]], baseline_path: Path) -> dict[str, Any]:
    current = {row["page_no"]: row for row in rows}
    baseline = {row["page_no"]: row for row in read_jsonl(baseline_path)}
    comparisons = []
    for page in BASELINE_PAGES:
        old, new = baseline.get(page), current.get(page)
        order_same = bool(old and new and old.get("reading_order") == new.get("reading_order"))
        types_same = bool(old and new and old.get("classifications") == new.get("classifications"))
        item = {"page_no": page, "reading_order_same": order_same,
                "classifications_same": types_same, "changed": not (order_same and types_same)}
        if item["changed"]:
            item["baseline"] = {"reading_order": old.get("reading_order") if old else None,
                                "classifications": old.get("classifications") if old else None}
            item["full_run"] = {"reading_order": new.get("reading_order") if new else None,
                                "classifications": new.get("classifications") if new else None}
        comparisons.append(item)
    return {"baseline_source": str(baseline_path), "pages_compared": BASELINE_PAGES,
            "changed_pages": [item["page_no"] for item in comparisons if item["changed"]],
            "comparisons": comparisons,
            "note": "Differences are audit signals; temperature=0 does not guarantee provider-level identity."}


def build_full_check(page_records: list[dict[str, Any]], rows: list[dict[str, Any]],
                     selected: list[int], output_dir: Path, usage: dict[str, Any],
                     settings: ParserVLMSettings) -> dict[str, Any]:
    pages = {record["page_no"]: record for record in page_records}; row_pages = [r["page_no"] for r in rows]
    validation_errors = {}
    covered_returned = []; table_changed = []
    for row in rows:
        page = pages[row["page_no"]]; active = active_blocks(page)
        covered = {b["block_id"] for b in page["blocks"] if "COVERED_BY_TABLE" in b.get("flags", [])}
        returned = set(row.get("reading_order", [])) | {c.get("block_id") for c in row.get("classifications", [])}
        covered_returned.extend(sorted(covered & returned))
        classes = _classes(row)
        table_changed.extend(b["block_id"] for b in active if b["kind"] == "table" and
                             classes.get(b["block_id"], {}).get("type") != "table")
        if row["status"] == "SUCCESS":
            value = {k: row.get(k) for k in ("page_no", "reading_order", "classifications", "page_type", "confidence")}
            errors = validate_page_structure(value, row["page_no"], active, covered)
            if errors: validation_errors[str(row["page_no"])] = errors
    output_files = [path for path in output_dir.rglob("*") if path.is_file()]
    secret_leaked = any(settings.api_key and settings.api_key in path.read_text(encoding="utf-8", errors="ignore")
                        for path in output_files)
    skipped = sorted(set(pages) - set(selected))
    checks = {
        "selected_active_pages_169": len(selected) == 169,
        "blank_pages_9_73_121_not_selected": skipped == [9, 73, 121],
        "one_result_per_selected_page": len(rows) == len(selected) and len(row_pages) == len(set(row_pages)) and set(row_pages) == set(selected),
        "all_pages_have_explicit_status": all(r["status"] in {"SUCCESS", "INVALID", "TECHNICAL_FAILED"} for r in rows),
        "all_success_pages_pass_local_validation": not validation_errors,
        "covered_by_table_never_returned": not covered_returned,
        "table_type_never_changed": not table_changed,
        "api_key_not_leaked": not secret_leaked,
        "usage_complete": usage.get("usage_complete") is True,
        "raw_response_for_every_selected_page": all((output_dir / "raw_responses" / f"page_{p:04d}.json").is_file() for p in selected),
        "request_audit_for_every_selected_page": all((output_dir / "request_audit" / f"page_{p:04d}.json").is_file() for p in selected),
        "incremental_results_complete": len(rows) == len(selected),
        "resume_accounting_consistent": usage.get("pages_called_this_run", 0) + usage.get("pages_reused_from_resume", 0) == len(selected),
        "no_renderer_or_downstream_output": not any((output_dir / n).exists() for n in
            ("good.md", "source_map.jsonl", "chunks.jsonl", "predictions.jsonl")),
    }
    statuses = Counter(r["status"] for r in rows)
    hard_ok = all(checks.values())
    return {"status": "PASS" if hard_ok and not statuses["INVALID"] and not statuses["TECHNICAL_FAILED"] else "COMPLETE_WITH_ISSUES",
            "phase": "A2-Full", "checks": checks, "selected_pages": len(selected),
            "skipped_pages": skipped, "status_counts": dict(statuses),
            "invalid_pages": [r["page_no"] for r in rows if r["status"] == "INVALID"],
            "technical_failed_pages": [r["page_no"] for r in rows if r["status"] == "TECHNICAL_FAILED"],
            "success_validation_errors": validation_errors, "covered_ids_returned": covered_returned,
            "table_ids_changed": table_changed, "parser_vlm_config": settings.public_dict()}


def generate_full_reports(page_records: list[dict[str, Any]], rows: list[dict[str, Any]],
                          selected: list[int], output_dir: Path, usage: dict[str, Any],
                          settings: ParserVLMSettings, baseline_path: Path) -> dict[str, Any]:
    stats, over = build_statistics(page_records, rows, len(page_records), selected)
    risks = build_risk_pages(page_records, rows, over)
    suggestions = build_manual_suggestions(page_records, rows, risks)
    regression = build_baseline_regression(rows, baseline_path)
    check = build_full_check(page_records, rows, selected, output_dir, usage, settings)
    write_json(output_dir / "A2_Full结构统计.json", stats)
    write_json(output_dir / "A2_Full风险页.json", {"risk_page_count": len(risks),
               "suspect_over_heading_count": len(over), "pages": risks})
    write_json(output_dir / "A2_Full人工抽查建议.json", {"suggested_page_count": len(suggestions), "pages": suggestions})
    write_json(output_dir / "A2_Full_baseline_regression.json", regression)
    write_json(output_dir / "a2_full_check_report.json", check)
    lines = ["# A2-Full 验收说明", "", f"- 技术状态：{check['status']}",
             f"- PDF页数：{stats['pdf_pages']}", f"- active页数：{stats['active_pages']}",
             f"- SUCCESS：{stats['success_pages']}", f"- INVALID：{stats['invalid_pages']}",
             f"- TECHNICAL_FAILED：{stats['technical_failed_pages']}",
             f"- 风险页：{len(risks)}", f"- SUSPECT_OVER_HEADING：{len(over)}", "",
             "本文件先记录自动统计；最终结论需结合人工风险页视觉核验。", ""]
    (output_dir / "A2_Full验收说明.md").write_text("\n".join(lines), encoding="utf-8")
    return {"statistics": stats, "risks": risks, "suggestions": suggestions,
            "regression": regression, "check": check}
