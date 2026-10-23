import json
from pathlib import Path
from src.document_processing.content_units_v3 import build_table_units

ROOT=Path(__file__).resolve().parents[1]

def _units():
    p=ROOT/"outputs/stage2_v3_content_units/DOC011/content_units.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x]

def test_doc011_applicability_content_anchor_preserved():
    matches=[u for u in _units() if "本项目不包含勘察" in u["text"]]
    assert len(matches)==1
    assert matches[0]["semantic_hints"]==["APPLICABILITY_STATEMENT_CANDIDATE"]

def test_doc011_amendment_cross_page_keeps_target_and_text():
    rows=[u for u in _units() if "AMENDMENT_ROW_CANDIDATE" in u["semantic_hints"] and u["table_context"]["cross_page"]]
    assert rows
    assert all(len({p["page_idx"] for p in u["provenance"]})>=2 for u in rows)
    assert any("条款号" in u["text"] and "补正后内容" in u["text"] for u in rows)

def test_award_parent_context_cross_page_fixture(tmp_path):
    middle={"pdf_info":[{"preproc_blocks":[{"type":"table","index":0,"blocks":[{"lines":[{"spans":[{"type":"table","html":"<table><tr><td>评分项</td><td>内容</td></tr><tr><td>获奖业绩（0分）</td><td>奖项清单</td></tr></table>"}]}]}]}]},{"preproc_blocks":[{"type":"table","index":0,"blocks":[{"lines":[{"spans":[{"type":"table","html":"<table><tr><td>评分项</td><td>内容</td></tr><tr><td></td><td>闽江杯、鲁班奖</td></tr></table>"}]}]}]}]}]}
    source=tmp_path/"middle.json"; source.write_text(json.dumps(middle,ensure_ascii=False),encoding="utf-8")
    tree={"document_id":"FIX","sections":[{"section_id":"S1","title":"评分标准","section_role":"TOPIC_SECTION","source":"TEST","parent_id":None,"depth":1,"range":{"start":[0,0,0],"end_exclusive":[2,0,0]},"canonical_position":[0,0,0],"content_block_ids":[]}],"body":{"root_section_ids":["S1"]}}
    table={"table_group_id":"TG1","page_start":0,"page_end":1,"canonical_target_ids":["S1"],"physical_fragment_source_atom_ids":["A0","A1"],"text":"评分表"}
    sm=[{"source_atom_id":f"A{i}","source_file":str(source),"page_idx":i,"source_payload_ref":{"collection":"preproc","index":0},"bbox_pdf":[0,0,100,100]} for i in range(2)]
    units,_,_=build_table_units(tree,[table],sm)
    cross=[u for u in units if u["table_context"]["cross_page"]]
    assert cross and "获奖业绩（0分）" in cross[0]["table_context"]["parent_row_context"]
    assert {p["page_idx"] for p in cross[0]["provenance"]}=={0,1}
