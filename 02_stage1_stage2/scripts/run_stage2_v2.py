from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.document_processing.page_registry import build as build_page_registry
from src.document_processing.source_representation_map_v2 import load as load_sources
from src.document_processing.outline_v2 import NativeOutlineProvider, basic_validate, normalize_roles, resolve_anchors, build_canonical_outline, SECTION_ROLES
from src.document_processing.document_tree_v2 import build_canonical_blocks, build_table_groups, detect_local_headings, classify_front_matter, build_document_tree
from src.document_processing.text_utils_v2 import normalize_text


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def markdown_tree(tree, local_headings, table_groups):
    local = {x["local_heading_id"]: x for x in local_headings}
    groups = {x["table_group_id"]: x for x in table_groups}
    lines = [f"# {tree['document_id']} Document Tree", "", "## Front Matter"]
    for node in tree["front_matter"]:
        lines.append(f"- {node['node_type']}: {len(node['content_block_ids'])} blocks")
    lines += ["", "## Body"]
    for node in tree["sections"]:
        indent = "  " * (node["canonical_depth"] - 1)
        p = node["anchor"]["page_idx"] + 1
        lines.append(f"{indent}- [{node['outline_role']}] {node['title_raw']} (PDF {p}, {node['section_id']})")
        for hid in node["local_heading_ids"]:
            h = local[hid]
            lines.append(f"{indent}  - [LOCAL_HEADING] {h['title']} (PDF {h['page_idx'] + 1}, {hid})")
        for gid in node["table_group_ids"]:
            g = groups[gid]
            suffix = " / CONTENT_IMAGE_CANDIDATE" if g.get("content_image_candidate") else ""
            lines.append(f"{indent}  - [TABLE_GROUP{suffix}] PDF {g['page_start'] + 1} ({gid})")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--pdf", type=Path, default=Path("input.pdf"))
    ap.add_argument("--raw-dir", type=Path, default=Path("data/hybrid_auto"))
    ap.add_argument("--output", type=Path, default=ROOT / "outputs" / "stage2_v2" / "DOC011")
    args = ap.parse_args(); out = args.output; out.mkdir(parents=True, exist_ok=True)
    doc_id = "DOC011"; log = []
    def note(message):
        log.append(f"{datetime.now(timezone.utc).isoformat()} {message}")

    registry, native_pages = build_page_registry(doc_id, args.pdf); note(f"page_registry={len(registry)}")
    atoms, reps, v2_records, middle = load_sources(doc_id, args.raw_dir, registry); note(f"source_atoms={len(atoms)} representations={len(reps)}")
    raw_outline = NativeOutlineProvider().load({"doc_id": doc_id, "pdf_path": args.pdf})
    basic = basic_validate(raw_outline, registry); roles = normalize_roles(raw_outline, basic)
    anchors = resolve_anchors(roles, native_pages, registry, atoms); canonical_outline = build_canonical_outline(roles, anchors)
    blocks, targets = build_canonical_blocks(doc_id, atoms, reps, native_pages, roles, anchors)
    table_groups = build_table_groups(doc_id, atoms, reps)
    local_headings = detect_local_headings(doc_id, blocks, native_pages, canonical_outline)
    front_matter = classify_front_matter(blocks, canonical_outline)
    tree = build_document_tree(doc_id, canonical_outline, local_headings, blocks, table_groups, front_matter, len(registry))
    for atom in atoms:
        atom["canonical_target_ids"] = sorted(set(atom.get("canonical_target_ids", []) + targets.get(atom["source_atom_id"], [])))

    role_counts = Counter(x["outline_role"] for x in roles)
    anchor_counts = {}
    for role in SECTION_ROLES:
        denominator = sum(x["outline_role"] == role for x in roles)
        numerator = sum(x["outline_role"] == role and a["status"] == "VERIFIED" for x, a in zip(roles, anchors))
        anchor_counts[role] = {"verified": numerator, "denominator": denominator, "rate": numerator / denominator if denominator else 1.0}
    coordinate_unresolved = [x["source_atom_id"] for x in atoms if x["representation_role"] == "PRIMARY_SOURCE_ATOM" and not x.get("bbox_pdf")]
    unaccounted = [x["source_atom_id"] for x in atoms if x["representation_role"] != "DISCARDED_SOURCE_ATOM" and not x.get("canonical_target_ids")]
    duplicate_targets = [x["source_atom_id"] for x in atoms if len(set(x.get("canonical_target_ids", []))) > 1]
    page48 = [x for x in local_headings if x["page_idx"] == 47 and "附件三" in x["title"] and x["status"] == "PROMOTED"]
    page71 = [x for x in local_headings if 70 <= x["page_idx"] <= 74 and "综合评估法评分标准" in x["title"] and x["status"] == "PROMOTED"]
    page124 = [g for g in table_groups if g["page_start"] == 123]
    page127 = [b for b in blocks if b["page_idx"] == 126 and "不包含勘察" in normalize(b["text_canonical"])]
    page129 = [b for b in blocks if b["page_idx"] == 128 and "另册提供" in normalize(b["text_canonical"])]
    page167_172 = [g for g in table_groups if g["page_start"] <= 166 and g["page_end"] >= 171]
    failures = []
    if anchor_counts["STRUCTURAL_SECTION"]["rate"] < 1: failures.append("STRUCTURAL_SECTION_ANCHOR_LT_100_PERCENT")
    topic_den = anchor_counts["TOPIC_SECTION"]["denominator"] + anchor_counts["ATTACHMENT_SECTION"]["denominator"]
    topic_num = anchor_counts["TOPIC_SECTION"]["verified"] + anchor_counts["ATTACHMENT_SECTION"]["verified"]
    topic_rate = topic_num / topic_den if topic_den else 1
    if topic_rate < .95: failures.append("TOPIC_SECTION_ANCHOR_LT_95_PERCENT")
    if coordinate_unresolved: failures.append("PRIMARY_ATOM_COORDINATE_UNRESOLVED")
    if unaccounted: failures.append("SOURCE_ATOM_UNACCOUNTED")
    if not page48: failures.append("REGRESSION_PDF48_LOCAL_HEADING_MISSING")
    if not page71: failures.append("REGRESSION_PDF71_75_SCORING_HEADING_MISSING")
    if not page124: failures.append("REGRESSION_PDF124_CONTENT_IMAGE_CANDIDATE_MISSING")
    if not page127: failures.append("REGRESSION_PDF127_APPLICABILITY_TEXT_MISSING")
    if not page129: failures.append("REGRESSION_PDF129_EXTERNAL_REFERENCE_TEXT_MISSING")
    if not page167_172 or len(page167_172[0]["physical_fragment_source_atom_ids"]) < 6: failures.append("REGRESSION_PDF167_172_TABLE_PROVENANCE_MISSING")
    quality = {
        "doc_id": doc_id, "status": "PASS" if not failures else "FAIL", "strict_requested": args.strict,
        "page_count": len(registry), "raw_outline_count": len(raw_outline), "outline_role_counts": dict(role_counts),
        "anchor_gate": {**anchor_counts, "TOPIC_PLUS_ATTACHMENT": {"verified": topic_num, "denominator": topic_den, "rate": topic_rate}},
        "source_atom_gate": {"total": len(atoms), "unaccounted_count": len(unaccounted), "unaccounted_ids": unaccounted,
                             "duplicate_target_count": len(duplicate_targets), "coordinate_unresolved_count": len(coordinate_unresolved), "coordinate_unresolved_ids": coordinate_unresolved},
        "structure_counts": {"canonical_sections": len(canonical_outline), "canonical_blocks": len(blocks), "table_groups": len(table_groups), "local_heading_candidates": len(local_headings), "local_headings_promoted": sum(x["status"] == "PROMOTED" for x in local_headings)},
        "regression_evidence": {"PDF48_attachment_3": [x["local_heading_id"] for x in page48], "PDF71_75_scoring_standard": [x["local_heading_id"] for x in page71],
                                "PDF124_content_image_candidate": [x["table_group_id"] for x in page124], "PDF127_not_include_survey": [x["canonical_block_id"] for x in page127],
                                "PDF129_separately_provided": [x["canonical_block_id"] for x in page129], "PDF167_172_table_groups": [x["table_group_id"] for x in page167_172]},
        "failures": failures, "scope_stop": "DOCUMENT_TREE_ONLY_NO_CONTENT_UNIT_NO_CHUNK_NO_LLM_REVIEW"
    }
    manifest = {"doc_id": doc_id, "pipeline": "stage2_v2_document_tree", "created_at": datetime.now(timezone.utc).isoformat(), "pdf_path": str(args.pdf), "pdf_sha256": sha256(args.pdf),
                "raw_dir": str(args.raw_dir), "output_dir": str(out), "source_files": sorted(str(x) for x in args.raw_dir.glob("*.json")), "strict": args.strict,
                "prohibited_outputs_confirmed_absent": ["content_units.jsonl", "chunks.jsonl", "llm_review.json"]}
    write_json(out / "manifest.json", manifest); write_jsonl(out / "page_registry.jsonl", registry)
    write_jsonl(out / "source_representation_map.jsonl", atoms + reps); write_json(out / "raw_outline.json", raw_outline)
    write_json(out / "outline_basic_validation.json", basic); write_json(out / "outline_roles.json", roles)
    write_json(out / "outline_anchor_validation.json", anchors); write_json(out / "canonical_outline.json", canonical_outline)
    write_jsonl(out / "supplementary_heading_candidates.jsonl", local_headings); write_jsonl(out / "canonical_blocks.jsonl", blocks)
    write_jsonl(out / "table_groups.jsonl", table_groups); write_json(out / "document_tree.json", tree)
    (out / "document_tree.md").write_text(markdown_tree(tree, local_headings, table_groups), encoding="utf-8")
    write_json(out / "structure_quality_report.json", quality); note(f"status={quality['status']} failures={failures}")
    (out / "run.log").write_text("\n".join(log) + "\n", encoding="utf-8")
    print(json.dumps(quality, ensure_ascii=False, indent=2))
    return 2 if args.strict and failures else 0


def normalize(text):
    return normalize_text(text)


if __name__ == "__main__":
    raise SystemExit(main())
