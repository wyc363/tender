from __future__ import annotations

from typing import Any


def local_to_original_page(local_page_idx: int, original_start_page_idx: int) -> tuple[int, int]:
    original_page_idx = local_page_idx + original_start_page_idx
    return original_page_idx, original_page_idx + 1


def _logical(section: dict[str, Any], priority: str, basis: str = "located_section") -> dict[str, Any]:
    return {
        "role": section["section_role"], "priority": priority, "section_id": section.get("section_id"),
        "pdf_page_start": section["start_page_idx"] + 1, "pdf_page_end": section["end_page_idx"] + 1,
        "mineru_start": section["start_page_idx"], "mineru_end": section["end_page_idx"],
        "original_start_page_idx": section["start_page_idx"], "source_confidence": section.get("confidence", 0.6),
        "selection_basis": basis,
    }


def _merge_pages(pages: list[int], lower: int, upper: int, context: int = 1) -> list[tuple[int, int]]:
    expanded = sorted({p for hit in pages for p in range(max(lower, hit-context), min(upper, hit+context)+1)})
    if not expanded:
        return []
    result: list[tuple[int, int]] = []
    start = previous = expanded[0]
    for page in expanded[1:]:
        if page != previous + 1:
            result.append((start, previous))
            start = page
        previous = page
    result.append((start, previous))
    return result


def _keyword_ranges(section_map: dict[str, Any], role: str, terms: set[str], within_roles: set[str], priority: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for section in (s for s in section_map["sections"] if s["section_role"] in within_roles):
        hits = [p["page_idx"] for p in section_map.get("keyword_page_index", {}).get("pages", [])
                if p.get("section_id") == section["section_id"] and terms.intersection(p["hits"])]
        for start, end in _merge_pages(hits, section["start_page_idx"], section["end_page_idx"], context=1):
            fragment = dict(section)
            fragment["start_page_idx"], fragment["end_page_idx"], fragment["section_role"] = start, end, role
            basis = "anchor_hit_context" if priority == "conditional" else "lightweight_keyword_range_expansion"
            results.append(_logical(fragment, priority, basis))
    return results


def normalize_physical_jobs(logical_ranges: list[dict[str, Any]], merge_adjacent_gap_pages: int = 0) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for item in sorted(logical_ranges, key=lambda x: (x["mineru_start"], x["mineru_end"], x["range_id"])):
        if jobs and item["mineru_start"] <= jobs[-1]["mineru_end"] + 1 + merge_adjacent_gap_pages:
            job = jobs[-1]
            job["mineru_end"] = max(job["mineru_end"], item["mineru_end"])
            job["pdf_page_end"] = job["mineru_end"] + 1
            job["covered_roles"] = sorted(set(job["covered_roles"] + [item["role"]]))
            job["source_range_ids"].append(item["range_id"])
        else:
            jobs.append({"job_id": "", "mineru_start": item["mineru_start"], "mineru_end": item["mineru_end"],
                         "pdf_page_start": item["mineru_start"] + 1, "pdf_page_end": item["mineru_end"] + 1,
                         "covered_roles": [item["role"]], "source_range_ids": [item["range_id"]]})
    for index, job in enumerate(jobs, 1):
        job["job_id"] = f"J{index:02d}"
    return jobs


def build_parse_plan(section_map: dict[str, Any], review_point_name: str, policy: dict[str, Any], merge_adjacent_gap_pages: int = 0) -> dict[str, Any]:
    primary, required = list(policy.get("primary_sections", [])), list(policy.get("required_sections", []))
    triggered, conditional = list(policy.get("triggered_sections", [])), list(policy.get("conditional_sections", []))
    context = list(policy.get("context_on_demand_sections", []))
    by_role = {s["section_role"]: s for s in section_map["sections"]}
    logical: list[dict[str, Any]] = []
    for role in primary + required + triggered:
        if role in by_role:
            logical.append(_logical(by_role[role], "primary" if role in primary else "required" if role in required else "triggered"))
        elif role == "QUALIFICATION":
            logical += _keyword_ranges(section_map, role, set(policy.get("qualification_terms", [])),
                                       {"ANNOUNCEMENT", "BIDDER_NOTICE_CHAPTER", "BIDDER_NOTICE", "BIDDER_NOTICE_TABLE", "EVALUATION"}, "required")
    if "BIDDER_NOTICE_TABLE" in conditional:
        logical += _keyword_ranges(section_map, "BIDDER_NOTICE_TABLE", set(policy.get("range_anchor_terms", [])),
                                   {"BIDDER_NOTICE_TABLE", "BIDDER_NOTICE_CHAPTER", "BIDDER_NOTICE"}, "conditional")
    # One source hit may satisfy several semantic searches; retain its semantics once.
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in logical:
        key = (item["role"], item["priority"], item["mineru_start"], item["mineru_end"], item["selection_basis"])
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    logical = deduped
    logical.sort(key=lambda x: (x["mineru_start"], x["mineru_end"], x["role"]))
    for index, item in enumerate(logical, 1):
        item["range_id"] = f"R{index:02d}"
    jobs = normalize_physical_jobs(logical, merge_adjacent_gap_pages)
    missing_primary = [role for role in primary if role not in by_role]
    missing_required = [role for role in required if role not in by_role and not any(x["role"] == role for x in logical)]
    return {
        "schema_version": "2.0", "doc_id": section_map["doc_id"], "file_name": section_map["file_name"], "file_path": section_map["file_path"],
        "review_point": review_point_name, "review_item": policy["review_item"], "official_rule_ids": policy["official_rule_ids"],
        "logical_ranges": logical, "physical_jobs": jobs, "ranges": logical,
        "missing_primary_sections": missing_primary, "missing_required_sections": missing_required,
        "triggered_sections_found": [role for role in triggered if role in by_role],
        "conditional_sections_included": sorted({x["role"] for x in logical if x["priority"] == "conditional"}),
        "context_on_demand_sections_located": [role for role in context if role in by_role],
        "requires_manual_confirmation": bool(missing_primary or missing_required),
    }


def powershell_command(plan: dict[str, Any], job: dict[str, Any], output_root: str) -> str:
    input_path = plan["file_path"].replace('"', '`"')
    roles = "_".join(role.lower() for role in job["covered_roles"])
    output_path = f"{output_root}\\{plan['doc_id']}\\{job['job_id']}_{roles}".replace('"', '`"')
    return "\n".join(["mineru `", f'  -p "{input_path}" `', f'  -o "{output_path}" `',
                      f"  -s {job['mineru_start']} `", f"  -e {job['mineru_end']} `", "  -m auto `",
                      "  -b hybrid-engine `", "  --effort high `", "  -t true `", "  --image-analysis false"])
