"""Deterministic source scope for page-local VLM table repair."""
from __future__ import annotations
from typing import Any
from .deterministic_structure import active_blocks, heading_decision
from .structure_risk import detect_table_risk

X_OVERLAP_THRESHOLD = 0.65
TRAILING_FIRST_GAP = 0.03
TRAILING_NEXT_GAP = 0.03

def _bbox(block: dict[str, Any]) -> list[float] | None:
    value = block.get("bbox_norm")
    if not isinstance(value, list) or len(value) != 4: return None
    try: box = [float(x) for x in value]
    except (TypeError, ValueError): return None
    return box if box[2] > box[0] and box[3] > box[1] else None

def _x_overlap(a: list[float], b: list[float]) -> float:
    overlap = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    return overlap / min(a[2] - a[0], b[2] - b[0])

def _text_overlap_fraction(text_box: list[float], region: list[float]) -> float:
    ix = max(0.0, min(text_box[2], region[2]) - max(text_box[0], region[0]))
    iy = max(0.0, min(text_box[3], region[3]) - max(text_box[1], region[1]))
    return (ix * iy) / ((text_box[2] - text_box[0]) * (text_box[3] - text_box[1]))

def _high_confidence_heading(block: dict[str, Any], following: dict[str, Any] | None = None) -> bool:
    decision = heading_decision(block, following)
    return bool(decision["is_heading"] and decision["score"] >= 8)

def _has_heading_boundary(blocks: list[dict[str, Any]], first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_box, second_box = _bbox(first), _bbox(second)
    if not first_box or not second_box: return True
    between = [b for b in blocks if b.get("kind") == "text" and _bbox(b)
               and first_box[3] <= _bbox(b)[1] and _bbox(b)[3] <= second_box[1]]
    return any(_high_confidence_heading(block, between[i + 1] if i + 1 < len(between) else None)
               for i, block in enumerate(between))

def _cell_facts(table: dict[str, Any]) -> list[dict[str, Any]]:
    facts = []
    for cell in table.get("cells", []) if isinstance(table.get("cells"), list) else []:
        cell_id = cell.get("cell_id") or cell.get("block_id")
        if cell_id and _bbox(cell) and cell.get("text") is not None:
            facts.append({**cell, "block_id": str(cell_id), "kind": "cell"})
    return facts

def _group_tables(blocks: list[dict[str, Any]], tables: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    ordered = sorted((table for table in tables if _bbox(table)), key=lambda x: (_bbox(x)[1], _bbox(x)[0]))
    groups: list[list[dict[str, Any]]] = []
    for table in ordered:
        if not groups: groups.append([table]); continue
        previous = groups[-1][-1]
        if _x_overlap(_bbox(previous), _bbox(table)) >= X_OVERLAP_THRESHOLD and not _has_heading_boundary(blocks, previous, table):
            groups[-1].append(table)
        else: groups.append([table])
    return groups

def build_table_repair_scope(page: dict[str, Any]) -> dict[str, Any]:
    blocks = active_blocks(page); risk = detect_table_risk(page)
    tables = [block for block in blocks if block.get("kind") == "table" and _bbox(block)]
    if not risk["risk"]:
        return {"page_no": int(page["page_no"]), "risk": False, "risk_reasons": [], "scope_status": "TABLE_OK", "repairable": False, "table_groups": [], "excluded_source_ids": [], "reason": "TABLE_OK"}
    groups = []
    for index, table_group in enumerate(_group_tables(blocks, tables), 1):
        boxes = [_bbox(table) for table in table_group]
        region = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]
        table_ids = [table["block_id"] for table in table_group]; eligible = list(table_ids)
        reasons = ["SINGLE_TABLE_GROUP" if len(table_group) == 1 else "MULTI_FRAGMENT_TABLE_GROUP", "TABLE_SOURCES_ALWAYS_ELIGIBLE"]
        if len(table_group) == 1:
            cells = _cell_facts(table_group[0]); eligible.extend(cell["block_id"] for cell in cells)
            for block in blocks:
                box = _bbox(block)
                if block.get("kind") == "text" and box and _text_overlap_fraction(box, region) > 0: eligible.append(block["block_id"])
            if len(eligible) > len(table_ids): reasons.append("ACTUAL_TABLE_BBOX_OVERLAP")
        else:
            reasons += ["ALIGNED_TABLE_FRAGMENTS", "INTERSTITIAL_VERTICAL_BAND"]
            for block in blocks:
                box = _bbox(block)
                if block.get("kind") == "text" and box and region[1] <= box[1] and box[3] <= region[3] and _x_overlap(box, region) >= X_OVERLAP_THRESHOLD:
                    eligible.append(block["block_id"])
            last_bottom = region[3]
            trailing = sorted((b for b in blocks if b.get("kind") == "text" and _bbox(b) and _bbox(b)[1] >= last_bottom), key=lambda b: _bbox(b)[1])
            previous_bottom = last_bottom
            for tail_index, block in enumerate(trailing):
                box = _bbox(block); gap = box[1] - previous_bottom; limit = TRAILING_FIRST_GAP if tail_index == 0 else TRAILING_NEXT_GAP
                if gap > limit or _x_overlap(box, region) < X_OVERLAP_THRESHOLD: break
                if _high_confidence_heading(block): reasons.append("TRAILING_STOPPED_AT_HEADING"); break
                eligible.append(block["block_id"]); previous_bottom = box[3]
            if any(source_id not in table_ids and _bbox(next(b for b in blocks if b["block_id"] == source_id))[1] >= region[3] for source_id in eligible): reasons.append("SHORT_TRAILING_CONTINUATION")
        eligible_set = set(eligible)
        # Preserve A1 page order in the request. Scope is a set constraint, while
        # stable visual order prevents two equivalent candidate permutations.
        eligible = [block["block_id"] for block in blocks if block["block_id"] in eligible_set]
        group_repairable = len(eligible) > len(table_ids)
        groups.append({"group_id": f'P{int(page["page_no"]):04d}-TG{index:03d}', "group_type": "SINGLE_TABLE_GROUP" if len(table_group) == 1 else "MULTI_FRAGMENT_TABLE_GROUP", "table_source_ids": table_ids, "eligible_source_ids": eligible, "region_bbox": region, "repairable": group_repairable, "scope_reasons": reasons})
    repairable = bool(groups) and all(group["repairable"] for group in groups)
    status = "REPAIRABLE_TABLE_RISK" if repairable else "UNREPAIRABLE_TABLE_RISK"
    eligible_set = {source_id for group in groups for source_id in group["eligible_source_ids"]}
    excluded = [b["block_id"] for b in blocks if b["block_id"] not in eligible_set]
    return {"page_no": int(page["page_no"]), "risk": True, "risk_reasons": risk["reasons"], "scope_status": status, "repairable": repairable, "table_groups": groups, "excluded_source_ids": excluded, "reason": "ELIGIBLE_SOURCE_FACTS_AVAILABLE" if repairable else "NO_ELIGIBLE_SOURCE_FACTS"}

def eligible_blocks(page: dict[str, Any], scope: dict[str, Any]) -> list[dict[str, Any]]:
    by_id = {b["block_id"]: b for b in active_blocks(page)}
    ids = [source_id for group in scope.get("table_groups", []) for source_id in group.get("eligible_source_ids", [])]
    return [by_id[source_id] for source_id in ids if source_id in by_id]

def validate_table_repair_scope(value: dict[str, Any], page: dict[str, Any], scope: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    active = {b["block_id"]: b for b in active_blocks(page)}
    covered = {b["block_id"] for b in page.get("blocks", []) if "COVERED_BY_TABLE" in b.get("flags", [])}
    groups = {g["group_id"]: g for g in scope.get("table_groups", [])}
    eligible_to_group = {source_id: group_id for group_id, group in groups.items() for source_id in group["eligible_source_ids"]}
    errors: list[str] = []; safety: list[dict[str, Any]] = []; used: list[str] = []
    for index, repair in enumerate(value.get("tables", []) if isinstance(value.get("tables"), list) else []):
        returned = repair.get("source_ids", []) if isinstance(repair, dict) else []
        group_ids = {eligible_to_group.get(source_id) for source_id in returned if source_id in eligible_to_group}
        out_of_scope = [source_id for source_id in returned if source_id not in eligible_to_group]
        unknown = [source_id for source_id in returned if source_id not in active]
        covered_ids = [source_id for source_id in returned if source_id in covered]
        if out_of_scope: errors.append(f"TABLE_{index}_OUT_OF_SCOPE_ID")
        if unknown: errors.append(f"TABLE_{index}_UNKNOWN_ID")
        if covered_ids: errors.append(f"TABLE_{index}_COVERED_ID")
        if len(group_ids) != 1: errors.append(f"TABLE_{index}_CROSS_GROUP")
        group_id = next(iter(group_ids)) if len(group_ids) == 1 else None
        table_ids = groups[group_id]["table_source_ids"] if group_id else []
        if not any(source_id in table_ids for source_id in returned): errors.append(f"TABLE_{index}_MISSING_TABLE_SOURCE")
        used.extend(returned)
        invalid = bool(out_of_scope or unknown or covered_ids or len(group_ids) != 1 or not any(x in table_ids for x in returned))
        safety.append({"group_id": group_id, "table_source_ids": table_ids, "eligible_source_ids": groups[group_id]["eligible_source_ids"] if group_id else [], "vlm_returned_source_ids": returned, "out_of_scope_ids": out_of_scope, "validation_status": "INVALID_REPAIR_SCOPE" if invalid else "VALID", "fallback_used": invalid})
    if len(used) != len(set(used)): errors.append("TABLE_REPAIR_SOURCE_REUSED")
    if errors:
        for row in safety: row["validation_status"] = "INVALID_REPAIR_SCOPE"; row["fallback_used"] = True
    return list(dict.fromkeys(errors)), safety

def annotate_valid_repairs(value: dict[str, Any], safety: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**repair, "group_id": row["group_id"], "validation_status": row["validation_status"]} for repair, row in zip(value.get("tables", []), safety)]
