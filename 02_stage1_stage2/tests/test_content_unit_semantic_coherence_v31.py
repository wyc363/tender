import copy,json
from pathlib import Path
from src.document_processing.content_unit_semantic_coherence_v31 import compose_form_instructions,split_internal_boundaries,merge_cross_page_text,selective_table_recovery,is_bare_instruction,semantic_metrics

ROOT=Path(__file__).resolve().parents[1]

def unit(uid,typ,text,page=0,section="S",local=None):
 return {"unit_id":uid,"document_id":"D","unit_type":typ,"section_id":section,"section_path":["格式"],"local_heading_context":local or [],"text":text,"normalized_text":" ".join(text.split()),"page_start":page,"page_end":page,"primary_source_block_ids":[uid+"B"],"context_source_block_ids":[],"source_table_group_ids":[],"source_atom_ids":[uid+"A"],"native_span_refs":[],"provenance":[{"page_idx":page,"bbox_pdf":[0,0,1,1],"canonical_block_id":uid+"B"}],"list_items":[],"quality_flags":[],"reconstruction_status":"OK"}

def test_bare_form_instruction_composes_list():
 a=unit("U1","FORM_INSTRUCTION_UNIT","注：");b=unit("U2","LIST_GROUP_UNIT","（1）A\n（2）B")
 out,audit,_=compose_form_instructions([a,b]);assert len(out)==1 and not is_bare_instruction(out[0]) and len(out[0]["primary_source_block_ids"])==2 and audit

def test_instruction_paragraph_composes():
 out,_,_=compose_form_instructions([unit("U1","FORM_INSTRUCTION_UNIT","说明："),unit("U2","PARAGRAPH_UNIT","投标人应提供证明材料。")]);assert "证明材料" in out[0]["text"]

def test_inline_heading_split_and_offsets():
 text="上一段结束。\n（三）新标题\n内容A。\n（四）另一个标题\n内容B。"
 out,audit,_=split_internal_boundaries([unit("U1","PARAGRAPH_UNIT",text)],"D");assert len(out)>=2 and audit and all("source_text_span" in x for x in out)

def test_list_numbers_are_not_inline_headings():
 text="投标人应提供：\n（1）A\n（2）B\n（3）C"
 out,audit,_=split_internal_boundaries([unit("U1","LIST_GROUP_UNIT",text)],"D");assert len(out)==1 and not audit

def test_cross_page_short_continuation_merges():
 a=unit("U1","LIST_GROUP_UNIT","（五）应配置必要的材料、设备",0);b=unit("U2","PARAGRAPH_UNIT","等。",1)
 out,audit,_=merge_cross_page_text([a,b]);assert len(out)==1 and out[0]["cross_page"] and {p["page_idx"] for p in out[0]["provenance"]}=={0,1} and audit

def test_cross_page_true_new_clause_does_not_merge():
 a=unit("U1","PARAGRAPH_UNIT","完整句结束。",0);b=unit("U2","PARAGRAPH_UNIT","1.新的条款。",1)
 out,audit,_=merge_cross_page_text([a,b]);assert len(out)==2 and not audit

def test_selective_table_recovery_uses_logical_bbox_evidence():
 u=unit("U1","TABLE_ROW_GROUP_UNIT","待恢复");u.update(source_table_group_ids=["TG"],source_atom_ids=["A"],reconstruction_status="RECOVERY_REQUIRED",quality_flags=["TABLE_ROW_RECOVERY_REQUIRED"])
 tables=[{"table_group_id":"TG","logical_representation_ids":["R"],"physical_fragment_source_atom_ids":["A"]}]
 sm=[{"representation_id":"R","html":"<table><tr><td>列A</td><td>列B</td></tr></table>","page_idx_hint":0},{"source_atom_id":"A","page_idx":0,"bbox_pdf":[0,0,10,10]}]
 out,audit,_=selective_table_recovery([u],tables,sm);assert out[0]["reconstruction_status"]=="OK" and out[0]["recovery_method"]=="V2_LOGICAL" and audit[0]["success"]

def test_selective_recovery_does_not_fabricate():
 u=unit("U1","TABLE_ROW_GROUP_UNIT","待恢复");u.update(source_table_group_ids=["TG"],source_atom_ids=[],reconstruction_status="RECOVERY_REQUIRED")
 out,audit,_=selective_table_recovery([u],[{"table_group_id":"TG","logical_representation_ids":[]}],[]);assert out[0]["reconstruction_status"]=="RECOVERY_REQUIRED" and "failure_reason" in audit[0]

def test_doc011_recovery_units_closed_with_allowed_methods():
 rows=[json.loads(x) for x in (ROOT/"outputs/stage2_v3_1_content_units/DOC011/selective_table_recovery_audit.jsonl").read_text(encoding="utf-8").splitlines()]
 by={x["table_group_id"]:x for x in rows};assert by["DOC011-TG00006"]["success"] and by["DOC011-TG00015"]["success"]
 assert by["DOC011-TG00006"]["priority"]=="HIGH"
 assert {by[x]["recovery_method"] for x in by}<={"V2_LOGICAL","MIDDLE_PHYSICAL","PDF_NATIVE","SELECTIVE_OCR","SELECTIVE_IMAGE_TABLE_RECOVERY"}

def test_doc011_semantic_gate_metrics_zero():
 report=json.loads((ROOT/"outputs/stage2_v3_1_content_units/DOC011/content_unit_quality_report_v3_1.json").read_text(encoding="utf-8"));m=report["metrics"]
 for key in ["bare_form_instruction_unit_count","orphan_continuation_fragment_count","unit_contains_multiple_high_confidence_internal_heading_count","recovery_required_high_priority_content_count"]:assert m[key]==0
