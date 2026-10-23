from __future__ import annotations

import re
from typing import Any


def trace_mismatches(tree:dict[str,Any], units:list[dict[str,Any]], front:list[dict[str,Any]], events:list[dict[str,Any]]) -> list[dict[str,Any]]:
    """Generic invariant tracer. Its results are audit evidence, never render decisions."""
    traces=[]; sections={s["section_id"]:s for s in tree["sections"]}; event_by_unit={e.get("unit_id"):e for e in events}
    if any(x.get("status")=="PRESENT" for x in front):
        traces.append({"error":"Front matter was absent from the prior readable view","failure_class":"FRONT_MATTER_OMITTED","first_bad_stage":"Readable Export","source_ids":[p["canonical_block_id"] for x in front for p in x.get("provenance",[])]})
    canonical=[]
    for unit in units:
        if unit.get("unit_type")=="TABLE_ROW_GROUP_UNIT": continue
        section=sections.get(unit.get("section_id"),{}); a=re.match(r"^(\d+)",unit.get("clause_ref_normalized") or "");b=re.match(r"^(\d+)\s*[.．、]",section.get("title") or "")
        event=event_by_unit.get(unit["unit_id"],{})
        if a and b and a.group(1)!=b.group(1) and "NATIVE_READBACK_POSITION_PATCH" in event.get("quality_flags",[]):
            canonical.append(unit)
    if canonical:
        traces.append({"error":"Clause family conflicts with assigned section and native PDF position","failure_class":"UPSTREAM_CANONICAL_BLOCK_DEFECT","first_bad_stage":"Canonical Block",
                       "source_ids":sorted({x for u in canonical for x in u.get("primary_source_block_ids",[])+u.get("context_source_block_ids",[])}),"unit_ids":[u["unit_id"] for u in canonical]})
    continuation=[u for u in units if (u.get("table_context") or {}).get("continuation") and "\n" in (u.get("text") or "")]
    if continuation:
        traces.append({"error":"Continuation text exists outside frozen table_context.cells","failure_class":"TABLE_TEXT_CONSUMPTION_DEFECT","first_bad_stage":"Content Unit logical row","source_ids":sorted({x for u in continuation for x in u.get("source_table_group_ids",[])}),"unit_ids":[u["unit_id"] for u in continuation]})
    duplicated=[]
    for u in units:
        cells=list((u.get("table_context") or {}).get("cells",{}).values()); long=[str(x) for x in cells if len(str(x))>=15]
        if len(long)>1 and len(set(long))==1: duplicated.append(u)
    if duplicated:
        traces.append({"error":"A spanning table cell was expanded into duplicate cell values","failure_class":"TABLE_TEXT_CONSUMPTION_DEFECT","first_bad_stage":"Content Unit colspan expansion","source_ids":sorted({x for u in duplicated for x in u.get("source_table_group_ids",[])}),"unit_ids":[u["unit_id"] for u in duplicated]})
    return traces
