from __future__ import annotations

import re
from typing import Any


def _position(unit: dict[str, Any]) -> list[Any]:
    provenance = unit.get("provenance") or []
    first = provenance[0] if provenance else {}
    bbox = first.get("bbox_pdf") or [0, 0, 0, 0]
    return [first.get("page_idx", unit.get("page_start", 0)), bbox[1], 0, 0]


def _native_position(unit: dict[str, Any], pdf: Any | None) -> list[Any] | None:
    if pdf is None or unit.get("unit_type") == "TABLE_ROW_GROUP_UNIT": return None
    page_idx = unit.get("page_start"); text = " ".join((unit.get("text") or "").split())
    if page_idx is None or not text or page_idx >= len(pdf): return None
    for length in (36, 28, 20, 14):
        needle = text[:length]
        hits = pdf[page_idx].search_for(needle)
        if len(hits) == 1:
            return [page_idx, float(hits[0].y0), 0, 0]
    return None


def _needs_native_check(unit: dict[str, Any], sections: dict[str, dict[str, Any]]) -> bool:
    section = sections.get(unit.get("section_id"), {}); title = (section.get("title") or "").strip()
    clause = unit.get("clause_ref_normalized") or ""
    a = re.match(r"^(\d+)", clause); b = re.match(r"^(\d+)\s*[.．、]", title)
    return bool(a and b and a.group(1) != b.group(1))


def build_events(tree: dict[str, Any], units: list[dict[str, Any]], front_matter: list[dict[str, Any]], logical_rows: dict[str, dict[str, Any]], pdf: Any | None = None) -> list[dict[str, Any]]:
    events=[]; sections={s["section_id"]:s for s in tree["sections"]}
    for fm in front_matter:
        if fm.get("display"):
            prov=fm["provenance"][0]; box=prov.get("bbox_pdf") or [0,0,0,0]
            events.append({"event_type":"FRONT_MATTER_EVENT","event_id":fm["front_matter_id"],"canonical_position":[prov["page_idx"],box[1],0,0],"front_matter":fm,"quality_flags":[]})
    for section in tree["sections"]:
        events.append({"event_type":"SECTION_ANCHOR_EVENT","event_id":"SE-"+section["section_id"],"canonical_position":list(section["canonical_position"])+[0],
                       "section_id":section["section_id"],"section_title":section["title"],"section_depth":section["depth"],"quality_flags":[]})
    for unit in units:
        original=_position(unit); effective=list(original); flags=[]
        if _needs_native_check(unit,sections):
            found=_native_position(unit,pdf)
            if found: effective=found; flags.append("NATIVE_READBACK_POSITION_PATCH")
            else: flags.append("NATIVE_READBACK_POSITION_UNRESOLVED")
        span=unit.get("source_text_span") or [0,0]; effective[3]=int(span[0] or 0)
        typ="TABLE_ROW_EVENT" if unit.get("unit_type")=="TABLE_ROW_GROUP_UNIT" else "CONTENT_UNIT_EVENT"
        events.append({"event_type":typ,"event_id":"CE-"+unit["unit_id"],"canonical_position":effective,"original_position":original,
                       "section_id":unit.get("section_id"),"unit_id":unit["unit_id"],"unit_type":unit.get("unit_type"),"text":unit.get("text", ""),
                       "logical_row":logical_rows.get(unit["unit_id"]),"quality_flags":flags})
    priority={"FRONT_MATTER_EVENT":0,"SECTION_ANCHOR_EVENT":1,"CONTENT_UNIT_EVENT":2,"TABLE_ROW_EVENT":2}
    events.sort(key=lambda e:(tuple(e["canonical_position"]),priority[e["event_type"]],e["event_id"]))
    for i,event in enumerate(events): event["event_index"]=i
    return events

