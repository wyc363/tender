from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
SRC = HERE / "src"
sys.path.insert(0, str(SRC))

from rp12_stage4e.asset_catalog import discover_documents  # noqa: E402
from rp12_stage4e.context_resolver import ContextResolver  # noqa: E402
from rp12_stage4e.rule_registry import build_scope_registry, write_registry  # noqa: E402


PROJECT = Path(r"D:\Users\Desktop\招标文件审查")
V2 = PROJECT / "v2优化"
RUN_ID = "stage4e01_offline_20261021"
OUT = HERE / "outputs" / RUN_ID
REPORTS = HERE / "reports"

FROZEN_INPUTS = V2 / "stage2_gold_baseline/inputs/frozen_24_judge_inputs.jsonl"
NEW_RULES = PROJECT / "招标文件审查规则20261013.xlsx"
MAPPING = V2 / "新旧规则映射表.xlsx"
DOC_OUTPUTS = PROJECT / "outputs/tender_review_system"

REAL_RUNS = {
    "EF-003": V2 / "stage4d1_round_b_entrypoint/outputs/ef003/run_20261021_065019",
    "EF-004": V2 / "stage4d1_round_b_entrypoint/outputs/ef004/run_20261021_065030",
    "EF-010": V2 / "stage4d_round_b_real_model/outputs/ef010/run_20261021_023111",
    "EF-011": V2 / "stage4b_preparation/outputs/real_model_ef011_4096_20261020_164337",
    "EF-016": V2 / "stage4d1_round_b_entrypoint/outputs/ef016/run_no_token_gate_20261021_072212",
    "EF-024": V2 / "stage4d1_round_b_entrypoint/outputs/ef024/run_no_token_gate_20261021_072234",
    "EF-026": V2 / "stage4d_round_b_real_model/outputs/ef026/run_20261021_021923",
}

FALLBACK_RISKS = {
    "EF-010": ["RULE_COVERAGE_UNRESOLVED", "LOCAL_POLICY_VALIDITY_NOT_CONFIRMED"],
    "EF-011": [],
    "EF-026": ["CONDITIONAL_TEMPLATE_ACTIVATION_UNRESOLVED", "PARENT_ZERO_CHILD_POSITIVE_CONFLICT", "PARENT_CHILD_ACTIVATION_UNRESOLVED"],
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)

    registry = build_scope_registry(NEW_RULES, MAPPING)
    write_registry(registry, OUT / "rp12_20261013_scope_registry.json")
    with (OUT / "rule_mapping_review.csv").open("w", encoding="utf-8-sig", newline="") as f:
        fields = ["rule_id", "scope_tier", "review_type", "review_point", "industry", "rule_text", "policy_verified", "policy_verification_status", "legacy_rule_ids", "scope_note"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for rule in registry["rules"]:
            w.writerow({
                "rule_id": rule["rule_id"], "scope_tier": rule["scope_tier"], "review_type": rule["review_type"],
                "review_point": rule["review_point"], "industry": rule["industry"], "rule_text": rule["rule_text"],
                "policy_verified": rule["policy_verified"], "policy_verification_status": rule["policy_verification_status"],
                "legacy_rule_ids": "|".join(str(x.get("legacy_rule_id")) for x in rule["legacy_mappings"]), "scope_note": rule["scope_note"],
            })

    frozen = {x["ef_id"]: x for x in read_jsonl(FROZEN_INPUTS)}
    documents = discover_documents(DOC_OUTPUTS)
    candidates_by_doc: dict[str, list[str]] = {}
    for row in frozen.values():
        candidates_by_doc.setdefault(row["document_id"], []).append(row["candidate_id"])
    manifest_rows = []
    for document_id, asset in sorted(documents.items()):
        manifest_rows.append({**asset, "frozen_candidate_ids": sorted(candidates_by_doc.get(document_id, []))})
    with (OUT / "candidate_context_manifest.jsonl").open("w", encoding="utf-8") as f:
        for row in manifest_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    traces = []
    case_diffs = []
    for ef_id, run_dir in REAL_RUNS.items():
        source = frozen[ef_id]
        document_id = source["document_id"]
        asset = documents.get(document_id)
        if asset is None:
            raise RuntimeError(f"DOCUMENT_ASSETS_NOT_FOUND:{document_id}")
        normalized = read_json(run_dir / "normalized_facts.json")
        facts = normalized.get("facts", [])
        annotations = {x.get("fact_id"): x for x in normalized.get("model_annotations", [])}
        for fact in facts:
            annotation = annotations.get(fact.get("fact_id"), {})
            if fact.get("activation_condition") is None and annotation.get("activation_condition") is not None:
                fact["activation_condition"] = annotation["activation_condition"]
        decision_path = run_dir / "candidate_decision.json"
        decision = read_json(decision_path) if decision_path.exists() else {}
        risks = decision.get("risk_codes_internal") or FALLBACK_RISKS.get(ef_id, [])
        anchor = (source.get("v0_judge_input_original") or {}).get("anchor_chunk_id")
        if not anchor:
            anchor = (source.get("readonly_v0_result_snapshot") or {}).get("anchor_chunk_id")
        resolver = ContextResolver.from_file(document_id, Path(asset["chunks_path"]))
        requests = resolver.infer_gaps(facts, risks)
        ef_traces = []
        for request in requests:
            trace = resolver.resolve(source["candidate_id"], anchor, request)
            trace.update({"ef_id": ef_id, "risk_codes_before": risks, "facts_sha256": sha256(run_dir / "normalized_facts.json")})
            traces.append(trace)
            ef_traces.append(trace)
        case_diffs.append({
            "ef_id": ef_id,
            "document_id": document_id,
            "candidate_id": source["candidate_id"],
            "anchor_chunk_id": anchor,
            "fact_count": len(facts),
            "risk_codes_before": risks,
            "context_requests": len(requests),
            "contexts_found": sum(x["context_found"] for x in ef_traces),
            "ambiguities_resolved": sum(x["resolved"] for x in ef_traces),
            "pages_added": sorted({p for x in ef_traces for p in x["page_numbers"]}),
            "retrieved_chunk_ids": sorted({c["chunk_id"] for x in ef_traces for c in x["retrieved_chunks"]}),
            "remaining_gap_reasons": sorted({x["gap_reason"] for x in ef_traces if not x["resolved"] and x.get("gap_reason")}),
            "business_decision_changed": False,
            "note": "Stage4E.1只补充上下文，不生成或改写LLM/业务结论。" if requests else "现有Stage4D.1无ContextResolver触发项。",
        })

    with (OUT / "context_retrieval_traces.jsonl").open("w", encoding="utf-8") as f:
        for row in traces:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    write_json(OUT / "real_7_context_before_after.json", case_diffs)

    expert_cases_path = V2 / "stage2_gold_baseline/gold/expert_cases.jsonl"
    expert_cases = read_jsonl(expert_cases_path)
    notes = {
        "source": str(expert_cases_path),
        "source_sha256": sha256(expert_cases_path),
        "use_restriction": "仅用于运行后评价；不得进入ContextResolver、模型Prompt或规则裁决条件。",
        "label_categories": ["expert_adoption", "explicit_fact_verdict", "policy_dispute", "unknown_gold"],
        "record_count": len(expert_cases),
        "strict_binary_gold_is_not_new_rule_gold": True,
    }
    write_json(OUT / "gold_label_scope_notes.json", notes)

    context_found = sum(x["context_found"] for x in traces)
    resolved = sum(x["resolved"] for x in traces)
    preflight = {
        "stage": "4E.0-4E.1",
        "status": "PASS" if len(REAL_RUNS) == 7 and len(manifest_rows) >= 19 and all(x["assets_complete"] for x in manifest_rows) else "PASS_WITH_ASSET_WARNINGS",
        "real_fact_case_count": len(REAL_RUNS),
        "document_asset_count": len(manifest_rows),
        "documents_assets_complete": sum(x["assets_complete"] for x in manifest_rows),
        "registry_counts": registry["counts"],
        "context_request_count": len(traces),
        "context_found_count": context_found,
        "ambiguity_resolved_count": resolved,
        "context_found_rate": context_found / len(traces) if traces else None,
        "ambiguity_resolved_rate": resolved / len(traces) if traces else None,
        "model_api_calls": 0,
        "network_requests": 0,
        "pdf_reruns": 0,
        "retrieval_reruns": 0,
        "production_switch": False,
    }
    write_json(OUT / "preflight_report.json", preflight)
    write_json(OUT / "input_manifest.json", {
        "stage4d1_source": str(V2 / "stage4d1_real_fact_feedback_fix/src/rp12_stage4d1"),
        "real_runs": {k: str(v) for k, v in REAL_RUNS.items()},
        "frozen_inputs": str(FROZEN_INPUTS),
        "new_rules": str(NEW_RULES),
        "mapping": str(MAPPING),
        "document_outputs": str(DOC_OUTPUTS),
    })
    print(json.dumps(preflight, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
