from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from document_processing.parse_planner import build_parse_plan, powershell_command
from document_processing.pdf_scanner import scan_pdf
from document_processing.section_locator import locate_sections


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def page_total(items: list[dict]) -> int:
    return sum(x["mineru_end"] - x["mineru_start"] + 1 for x in items)


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate Milestone A.2 plans; never executes MinerU.")
    ap.add_argument("--pdf-dir", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, default=ROOT / "outputs" / "milestone_a2")
    ap.add_argument("--review-point", default="regional_industry_award")
    args = ap.parse_args()
    policy = yaml.safe_load((ROOT / "configs/review_points.yaml").read_text(encoding="utf-8"))["review_points"][args.review_point]
    parser_config = yaml.safe_load((ROOT / "configs/parser.yaml").read_text(encoding="utf-8"))
    merge_gap = int(parser_config.get("parse_planner", {}).get("merge_adjacent_gap_pages", 0))
    pdfs = sorted(args.pdf_dir.resolve().glob("*.pdf"), key=lambda p: p.name)
    if len(pdfs) != 19:
        raise SystemExit(f"Expected exactly 19 PDFs, found {len(pdfs)} in {args.pdf_dir}")
    section_dir, plan_dir = args.output_dir / "section_maps", args.output_dir / "parse_plans"
    commands = ["# Milestone A.2 generated only. DO NOT execute before human approval.", "# MinerU was NOT executed.",
                "& 'D:\\Anaconda\\shell\\condabin\\conda-hook.ps1'", "conda activate mineru", ""]
    docs, duplicate_count = [], 0
    for i, pdf in enumerate(pdfs, 1):
        doc_id = f"DOC{i:03d}"
        scan = scan_pdf(pdf, doc_id)
        terms = list(dict.fromkeys(policy.get("keyword_index_terms", []) + policy.get("qualification_terms", []) + policy.get("conditional_reference_terms", [])))
        section_map = locate_sections(scan, terms)
        plan = build_parse_plan(section_map, args.review_point, policy, merge_gap)
        write_json(section_dir / f"{doc_id}_section_map.json", section_map)
        write_json(plan_dir / f"{doc_id}_parse_plan.json", plan)
        commands.append(f"# {doc_id}: {pdf.name}")
        for job in plan["physical_jobs"]:
            commands.extend([powershell_command(plan, job, str((ROOT / "data/mineru_raw").resolve())), ""])
        coverage: set[int] = set()
        duplicates: set[int] = set()
        for job in plan["physical_jobs"]:
            pages = set(range(job["mineru_start"], job["mineru_end"] + 1))
            duplicates |= coverage & pages
            coverage |= pages
        duplicate_count += len(duplicates)
        roles = {s["section_role"]: [s["pdf_page_start"], s["pdf_page_end"]] for s in section_map["sections"]}
        docs.append({"doc_id": doc_id, "page_count": scan["page_count"], "logical_range_count": len(plan["logical_ranges"]),
                     "physical_job_count": len(plan["physical_jobs"]), "scheduled_unique_pages": len(coverage),
                     "duplicate_page_executions": len(duplicates), "roles": roles,
                     "conditional_included": plan["conditional_sections_included"],
                     "manual_confirmation": plan["requires_manual_confirmation"]})
    (args.output_dir / "mineru_commands.ps1").write_text("\n".join(commands), encoding="utf-8-sig")
    a1_plans = sorted((ROOT / "outputs/parse_plans").glob("*_parse_plan.json"))
    a1_commands = sum(len(json.loads(p.read_text(encoding="utf-8")).get("ranges", [])) for p in a1_plans)
    a1_pages = sum(page_total(json.loads(p.read_text(encoding="utf-8")).get("ranges", [])) for p in a1_plans)
    a2_jobs = sum(d["physical_job_count"] for d in docs)
    a2_pages = sum(d["scheduled_unique_pages"] for d in docs)
    report = {"milestone": "A.2", "pdf_count": len(pdfs), "mineru_executed": False,
              "a1": {"command_count": a1_commands, "scheduled_page_executions": a1_pages},
              "a2": {"logical_range_count": sum(d["logical_range_count"] for d in docs), "physical_job_count": a2_jobs,
                     "scheduled_unique_pages": a2_pages, "duplicate_page_executions": duplicate_count},
              "reduction": {"commands": a1_commands-a2_jobs, "page_executions": a1_pages-a2_pages}, "documents": docs}
    write_json(args.output_dir / "parse_coverage_report.json", report)
    diff = {"baseline": "outputs (Milestone A.1)", "candidate": "outputs/milestone_a2", "documents": []}
    for d in docs:
        old_path = ROOT / "outputs/section_maps" / f"{d['doc_id']}_section_map.json"
        old_roles = {}
        if old_path.exists():
            old = json.loads(old_path.read_text(encoding="utf-8"))
            old_roles = {s["section_role"]: [s["pdf_page_start"], s["pdf_page_end"]] for s in old["sections"]}
        changes = {r: {"a1": old_roles.get(r), "a2": d["roles"].get(r)} for r in sorted(set(old_roles)|set(d["roles"])) if old_roles.get(r) != d["roles"].get(r)}
        diff["documents"].append({"doc_id": d["doc_id"], "section_changes": changes,
                                  "a2_logical_ranges": d["logical_range_count"], "a2_physical_jobs": d["physical_job_count"]})
    write_json(args.output_dir / "milestone_a2_diff.json", diff)
    print(json.dumps(report["a2"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
