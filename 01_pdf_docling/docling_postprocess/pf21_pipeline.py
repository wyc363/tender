"""PF-2.1 reports for the body-enumeration heading guard."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .deterministic_structure import active_blocks, build_final_page
from .vlm_structure import write_json


TARGET_PAGES = [29, 5, 12, 38, 58, 63, 122, 127, 146, 160, 165]
EXPECTED_PAGE29_IDS = [f"P0029-B{i:03d}" for i in range(4, 11)]


def _source_semantics(final: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for element in final.get("ordered_elements", []):
        for source_id in element.get("source_ids", []):
            result[source_id] = {
                "semantic_type": element.get("semantic_type"),
                "decision": element.get("decision"),
                "text": element.get("text", ""),
            }
    return result


def semantic_diff(old_finals: list[dict[str, Any]],
                  new_finals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare final semantic decisions by immutable source block ID."""
    old_pages = {int(page["page_no"]): page for page in old_finals}
    changes: list[dict[str, Any]] = []
    for new_page in new_finals:
        page_no = int(new_page["page_no"])
        old = _source_semantics(old_pages[page_no])
        new = _source_semantics(new_page)
        for source_id in sorted(set(old) | set(new)):
            before, after = old.get(source_id, {}), new.get(source_id, {})
            if (before.get("semantic_type"), before.get("decision")) == (
                    after.get("semantic_type"), after.get("decision")):
                continue
            changes.append({
                "page_no": page_no,
                "source_id": source_id,
                "old_semantic_type": before.get("semantic_type"),
                "new_semantic_type": after.get("semantic_type"),
                "old_decision": before.get("decision"),
                "new_decision": after.get("decision"),
                "text": after.get("text", before.get("text", "")),
            })
    return changes


def targeted_regression(records: list[dict[str, Any]], old_finals: list[dict[str, Any]],
                        output: Path, pages: list[int] | None = None) -> dict[str, Any]:
    """Run the deterministic parser only; this function has no API code path."""
    selected = pages or TARGET_PAGES
    by_page = {int(page["page_no"]): page for page in records}
    old_by_page = {int(page["page_no"]): page for page in old_finals}
    new_finals = [build_final_page(by_page[page_no]) for page_no in selected]
    old_selected = [old_by_page[page_no] for page_no in selected]
    changes = semantic_diff(old_selected, new_finals)
    unexpected = [row for row in changes
                  if not (row["page_no"] == 29 and row["source_id"] in EXPECTED_PAGE29_IDS)]
    page29 = _source_semantics(next(page for page in new_finals if page["page_no"] == 29))
    expected_fixed = all(
        page29.get(source_id, {}).get("semantic_type") == "text"
        and page29.get(source_id, {}).get("decision") == "BODY_ENUMERATION_LIST"
        for source_id in EXPECTED_PAGE29_IDS
    )
    status = "PASS" if expected_fixed and len(unexpected) <= 10 else "ENUMERATION_GUARD_TOO_BROAD"
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "PF2_1_Heading_Diff.json", {
        "status": status,
        "target_pages": selected,
        "api_calls": 0,
        "semantic_changes": changes,
        "semantic_changes_count": len(changes),
        "expected_page29_changes": len(changes) - len(unexpected),
        "non_page29_or_unexpected_changes": len(unexpected),
        "stop_threshold": 10,
    })
    report = {
        "status": status,
        "api_calls": 0,
        "pages_checked": selected,
        "page29_expected_blocks_fixed": expected_fixed,
        "unexpected_semantic_changes": unexpected,
        "new_final_pages": new_finals,
    }
    write_json(output / "PF2_1_Targeted_Check.json", report)
    return report


def enumeration_guard_report(finals: list[dict[str, Any]]) -> dict[str, Any]:
    runs = [run for final in finals for run in final.get("enumeration_runs", [])]
    return {
        "total_runs_detected": len(runs),
        "total_blocks_demoted": sum(len(run.get("run_source_ids", [])) for run in runs),
        "runs": runs,
    }


def consecutive_heading_runs(finals: list[dict[str, Any]], minimum: int = 5) -> list[dict[str, Any]]:
    """List remaining adjacent Markdown heading runs for human review."""
    found: list[dict[str, Any]] = []
    for final in finals:
        elements = final.get("ordered_elements", [])
        index = 0
        while index < len(elements):
            if elements[index].get("semantic_type") != "heading":
                index += 1
                continue
            end = index + 1
            while end < len(elements) and elements[end].get("semantic_type") == "heading":
                end += 1
            run = elements[index:end]
            if len(run) >= minimum:
                found.append({
                    "page_no": int(final["page_no"]),
                    "count": len(run),
                    "source_ids": [sid for element in run for sid in element.get("source_ids", [])],
                    "texts": [element.get("text", "") for element in run],
                })
            index = end
    return found


def write_pf21_reports(finals: list[dict[str, Any]], output: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    enumeration = enumeration_guard_report(finals)
    heading_runs = consecutive_heading_runs(finals)
    write_json(output / "PF2_1_Enumeration_Guard_Report.json", enumeration)
    return enumeration, heading_runs
