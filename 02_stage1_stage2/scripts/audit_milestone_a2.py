from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/milestone_a2"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    failures: list[str] = []
    maps = sorted((OUT / "section_maps").glob("*_section_map.json"))
    plans = sorted((OUT / "parse_plans").glob("*_parse_plan.json"))
    if len(maps) != 19 or len(plans) != 19:
        failures.append(f"expected 19 maps/plans, got {len(maps)}/{len(plans)}")
    for path in plans:
        plan = load(path)
        jobs = plan.get("physical_jobs", [])
        for left, right in zip(jobs, jobs[1:]):
            if right["mineru_start"] <= left["mineru_end"] + 1:
                failures.append(f"{plan['doc_id']}: overlapping/adjacent physical jobs")
        for logical in plan.get("logical_ranges", []):
            if not any(j["mineru_start"] <= logical["mineru_start"] and j["mineru_end"] >= logical["mineru_end"] and logical["range_id"] in j["source_range_ids"] for j in jobs):
                failures.append(f"{plan['doc_id']}: uncovered {logical['range_id']}")
        if any("CONTRACT" in j["covered_roles"] for j in jobs):
            failures.append(f"{plan['doc_id']}: CONTRACT scheduled by default")
    doc12 = load(OUT / "section_maps/DOC012_section_map.json")
    roles = {s["section_role"]: s for s in doc12["sections"]}
    expected = {"BIDDER_NOTICE_TABLE": (24, 50), "BIDDER_NOTICE": (51, 83), "EVALUATION": (84, 102)}
    for role, pages in expected.items():
        actual = (roles.get(role, {}).get("pdf_page_start"), roles.get(role, {}).get("pdf_page_end"))
        if actual != pages:
            failures.append(f"DOC012 {role}: expected {pages}, got {actual}")
    if any(s["section_role"] == "FORM_TEMPLATE" and s["pdf_page_start"] < 84 for s in doc12["sections"]):
        failures.append("DOC012 false FORM_TEMPLATE before EVALUATION")
    doc16 = load(OUT / "section_maps/DOC016_section_map.json")
    if not any(s["section_role"] in {"DETERMINATION", "AWARD_SELECTION"} for s in doc16["sections"]):
        failures.append("DOC016 determination section missing")
    doc16_plan = load(OUT / "parse_plans/DOC016_parse_plan.json")
    if not any("DETERMINATION" in j["covered_roles"] for j in doc16_plan["physical_jobs"]):
        failures.append("DOC016 determination not scheduled")
    coverage = load(OUT / "parse_coverage_report.json")
    if coverage["a2"]["duplicate_page_executions"] != 0:
        failures.append("duplicate page executions are not zero")
    result = {"milestone": "A.2", "status": "PASS" if not failures else "FAIL", "checks": {
        "section_maps": len(maps), "parse_plans": len(plans), "doc012_strict_boundaries": not any("DOC012" in x for x in failures),
        "doc016_determination": not any("DOC016" in x for x in failures), "duplicate_page_executions": coverage["a2"]["duplicate_page_executions"]},
        "failures": failures}
    (OUT / "audit_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
