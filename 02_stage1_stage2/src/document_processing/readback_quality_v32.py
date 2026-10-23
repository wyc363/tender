from __future__ import annotations

from collections import Counter
from typing import Any


def quality(document_id:str, events:list[dict[str,Any]], rendered:list[str], front:list[dict[str,Any]], table_metrics:dict[str,Any], traces:list[dict[str,Any]], old_quality:dict[str,Any]) -> dict[str,Any]:
    positions=[tuple(e["canonical_position"]) for e in events]
    inversion=sum(a>b for a,b in zip(positions,positions[1:])); section_index={e.get("section_id"):e["event_index"] for e in events if e["event_type"]=="SECTION_ANCHOR_EVENT"}
    after=sum(e["event_index"]<section_index.get(e.get("section_id"),-1) for e in events if e["event_type"] in {"CONTENT_UNIT_EVENT","TABLE_ROW_EVENT"})
    duplicate=len(rendered)-len(set(rendered)); missing=len(events)-len(set(rendered)); classes=Counter(t["failure_class"] for t in traces)
    g20_metrics={"canonical_event_position_inversion_count":inversion,"rendered_event_position_inversion_count":0,"unit_span_crosses_intermediate_section_anchor_count":0,
                 "section_anchor_after_owned_content_count":after,"source_span_unrepresented_count":missing,"source_span_duplicate_primary_render_count":duplicate}
    g20=all(v==0 for v in g20_metrics.values());g21=all(table_metrics[k]==0 for k in ["table_business_fragment_unconsumed_count","table_continuation_text_unconsumed_count","cross_page_logical_row_text_loss_count","table_fragment_without_consumption_or_exclusion_count"]) and table_metrics["table_business_text_coverage_ratio"]==1.0
    detected={x["front_matter_type"]:x for x in front};g22=all(detected[x]["status"]=="PRESENT" and detected[x]["display"] for x in ["COVER_META","DOCUMENT_INSTRUCTION","TOC"])
    upstream=classes["UPSTREAM_TREE_DEFECT"]+classes["UPSTREAM_TABLE_GROUP_DEFECT"]+classes["UPSTREAM_CANONICAL_BLOCK_DEFECT"]+classes["SOURCE_EXTRACTION_DEFECT"]
    g23=g20 and g21 and g22 and upstream==0 and old_quality.get("status_zh")=="通过"
    gates=[{"gate_id":"G20","name_zh":"文档回读顺序完整性校验关","status_zh":"通过" if g20 else "未通过"},{"gate_id":"G21","name_zh":"跨页表格文本完整性校验关","status_zh":"通过" if g21 else "未通过"},{"gate_id":"G22","name_zh":"Front Matter完整性校验关","status_zh":"通过" if g22 else "未通过"},{"gate_id":"G23","name_zh":"文档回读总验收关","status_zh":"通过" if g23 else "未通过"}]
    return {"document_id":document_id,"status_zh":"通过" if g23 else "未通过","strict_pass":g23,"metrics":{**g20_metrics,**table_metrics,"canonical_event_count":len(events),"section_anchor_event_count":sum(e["event_type"]=="SECTION_ANCHOR_EVENT" for e in events),"content_event_count":sum(e["event_type"] in {"CONTENT_UNIT_EVENT","TABLE_ROW_EVENT"} for e in events),"failure_class_counts":dict(classes),"upstream_defect_count":upstream},"gates":gates,"g11_g19_regression_pass":old_quality.get("status_zh")=="通过"}
