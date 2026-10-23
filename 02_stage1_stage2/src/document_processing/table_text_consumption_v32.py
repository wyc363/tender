from __future__ import annotations

from collections import defaultdict
from typing import Any


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def full_cells(unit: dict[str, Any]) -> dict[str, str]:
    """Return a readback overlay; never mutates the frozen unit."""
    context = unit.get("table_context") or {}
    cells = {str(k): _clean(v) for k, v in (context.get("cells") or {}).items()}
    headers = list(context.get("column_headers") or cells)
    if not context.get("continuation") or "\n" not in (unit.get("text") or ""):
        return cells
    # v3.1 appends continuation fragments to unit.text as labelled physical parts,
    # but leaves cells frozen at the first page. Consume only an explicit header label.
    for continuation in unit["text"].splitlines()[1:]:
        continuation = continuation.strip()
        for header in sorted(headers, key=len, reverse=True):
            prefix = f"{header}:"
            if continuation.startswith(prefix):
                value = _clean(continuation[len(prefix):])
                if value:
                    cells[header] = _clean(f"{cells.get(header, '')} {value}")
                break
    return cells


def span_overlay(cells: dict[str, str], headers: list[str]) -> list[dict[str, Any]]:
    values = [cells.get(h, "") for h in headers]
    nonempty = [v for v in values if v]
    if len(headers) > 1 and nonempty and len(set(nonempty)) == 1 and len(nonempty) == len(headers) and len(nonempty[0]) >= 15:
        return [{"value": nonempty[0], "column_start": 0, "colspan": len(headers)}]
    return [{"value": cells.get(header, ""), "column_start": i, "colspan": 1} for i, header in enumerate(headers)]


def build_logical_rows(units: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for unit in units:
        if unit.get("unit_type") != "TABLE_ROW_GROUP_UNIT":
            continue
        context = unit.get("table_context") or {}; headers = list(context.get("column_headers") or [])
        cells = full_cells(unit)
        result[unit["unit_id"]] = {
            "logical_row_id": unit["unit_id"], "table_group_id": (unit.get("source_table_group_ids") or [None])[0],
            "fragment_refs": list(unit.get("source_atom_ids") or []), "cell_refs": [],
            "inherited_headers": headers, "parent_row_context": list(context.get("parent_row_context") or []),
            "physical_text_parts": (unit.get("text") or "").splitlines(), "full_logical_text": unit.get("text") or "",
            "contextualized_text": context.get("text_for_retrieval") or unit.get("text") or "",
            "page_start": unit.get("page_start"), "page_end": unit.get("page_end"),
            "cross_page": bool(context.get("cross_page") or unit.get("page_start") != unit.get("page_end")),
            "headers": headers, "cells": cells, "render_cells": span_overlay(cells, headers),
        }
    return result


def consumption_audit(units: list[dict[str, Any]], row_audit: list[dict[str, Any]], logical_rows: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    units_by_id = {u["unit_id"]: u for u in units}; records = []
    for i, record in enumerate(row_audit, 1):
        classification = record.get("classification", "UNKNOWN")
        unit_id = record.get("unit_id"); unit = units_by_id.get(unit_id)
        excluded = classification in {"REPEATED_HEADER", "EMPTY", "STRUCTURAL_CONTEXT", "FOOTNOTE", "NOISE"}
        pages = {p.get("page_idx") for p in (unit or {}).get("provenance", [])}
        consumed = bool(unit_id in logical_rows and record.get("page_idx") in pages)
        records.append({**record, "fragment_record_id": f"TFR-{i:06d}", "cell_class": "BUSINESS_TEXT" if not excluded else classification,
                        "consumed_by_logical_row_id": unit_id if consumed else None,
                        "exclusion_reason": classification if excluded else None,
                        "status": "CONSUMED" if consumed else ("EXCLUDED" if excluded else "UNCONSUMED")})
    business = [r for r in records if r["cell_class"] == "BUSINESS_TEXT"]
    unconsumed = [r for r in business if not r["consumed_by_logical_row_id"]]
    continuation_unconsumed = [r for r in unconsumed if r.get("classification") == "CONTINUATION"]
    losses = []
    for unit in units:
        context = unit.get("table_context") or {}
        if unit.get("unit_type") == "TABLE_ROW_GROUP_UNIT" and context.get("continuation"):
            old = " ".join(_clean(v) for v in (context.get("cells") or {}).values())
            new = " ".join(full_cells(unit).values())
            if len(new) <= len(old): losses.append(unit["unit_id"])
    metrics = {
        "table_business_fragment_count": len(business),
        "table_business_fragment_consumed_count": len(business) - len(unconsumed),
        "table_business_fragment_unconsumed_count": len(unconsumed),
        "table_continuation_text_unconsumed_count": len(continuation_unconsumed),
        "cross_page_logical_row_text_loss_count": len(losses),
        "table_fragment_without_consumption_or_exclusion_count": sum(r["status"] == "UNCONSUMED" for r in records),
        "table_business_text_coverage_ratio": round((len(business)-len(unconsumed))/len(business), 6) if business else 1.0,
        "cross_page_logical_row_text_loss_ids": losses,
    }
    return records, metrics

