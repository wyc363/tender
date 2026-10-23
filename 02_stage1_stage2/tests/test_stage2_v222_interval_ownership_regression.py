import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]/"outputs/stage2_v2_2_2/DOC011"
def data(name):return json.loads((ROOT/name).read_text(encoding="utf-8"))
def test_two_gap_sections_have_exact_sibling_intervals_and_content():
    tree=data("document_tree_v2_2_2.json");nodes=tree["sections"]
    def norm(s):return "".join(s.split())
    def get(q):return next(x for x in nodes if q in norm(x["title"]))
    a,b,c=get("一、工程概况"),get("二、工程设计范围、阶段与服务内容"),get("三、工程设计周期")
    assert a["range"]["end_exclusive"]==b["range"]["start"]
    assert b["range"]["start"]==b["canonical_position"] and b["range"]["end_exclusive"]==c["range"]["start"]
    assert len(b["content_block_ids"])==2 and not set(b["content_block_ids"])&set(a["content_block_ids"])
    a,b,c=get("七、承诺"),get("八、词语含义"),get("九、签订地点")
    assert a["range"]["end_exclusive"]==b["range"]["start"] and b["range"]["end_exclusive"]==c["range"]["start"]
    assert len(b["content_block_ids"])==1 and not set(b["content_block_ids"])&set(a["content_block_ids"])
def test_all_promoted_sections_have_standard_derived_fields():
    sections=data("document_tree_v2_2_2.json")["sections"]
    required={"range","content_block_ids","table_group_ids"}
    assert all(required<=set(x) for x in sections if x["source"]!="NATIVE_OUTLINE")
def test_six_new_consistency_metrics_are_zero():
    m=data("structure_quality_report_v2_2_2.json")["metrics"]
    keys=["promoted_section_missing_range_count","promoted_section_missing_content_field_count","sibling_interval_contains_intermediate_section_anchor_count","content_block_owner_interval_mismatch_count","content_block_multiple_section_owner_count","table_group_parent_interval_mismatch_count"]
    assert all(m[k]==0 for k in keys)
def test_v221_and_earlier_regressions_remain_green():
    r=data("structure_quality_report_v2_2_2.json")
    assert r["status_zh"]=="通过" and not r["failures"]
    assert all(x["passed"] for x in r["previous_regressions"])
    p=r["previous_consistency_metrics"]
    assert p["local_heading_invalid_canonical_parent_count"]==0 and p["tree_depth_inconsistency_count"]==0 and p["high_confidence_numbering_gap_not_promoted_count"]==0
