from __future__ import annotations
import argparse,hashlib,json,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.document_processing.content_unit_semantic_coherence_v31 import selective_table_recovery,compose_form_instructions,split_internal_boundaries,merge_cross_page_text,semantic_metrics
from src.document_processing.content_units_v3 import quality_report

def j(p):return json.loads(p.read_text(encoding="utf-8"))
def jl(p):return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x]
def wj(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding="utf-8")
def wjl(p,x):p.write_text("".join(json.dumps(y,ensure_ascii=False)+"\n" for y in x),encoding="utf-8")
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def markdown(units,report):
 lines=[f"# {report['document_id']} Stage2-v3.1 Content Units","",f"- Status: {report['status_zh']}",f"- Units: {len(units)}",""]
 current=None
 for u in units:
  if u["section_id"]!=current: current=u["section_id"];lines += [f"## {' / '.join(u['section_path'])}",""]
  lines += [f"### {u['unit_id']} [{u['unit_type']}] PDF {u['page_start']+1}-{u['page_end']+1}","",u["text"],""]
 return "\n".join(lines)
def main():
 ap=argparse.ArgumentParser();ap.add_argument("--strict",action="store_true")
 for name in ("v3","tree","blocks","tables","source-map","outline-roles","output"):ap.add_argument("--"+name,type=Path,required=True)
 a=ap.parse_args()
 inputs=[a.v3/"content_units.jsonl",a.v3/"content_unit_quality_report.json",a.tree,a.blocks,a.tables,a.source_map,a.outline_roles]
 if any(not p.exists() for p in inputs):return 3
 before={str(p.resolve()):sha(p) for p in inputs};a.output.mkdir(parents=True,exist_ok=True)
 old=jl(inputs[0]);tree=j(a.tree);blocks=jl(a.blocks);tables=jl(a.tables);source_map=jl(a.source_map);outline_roles=j(a.outline_roles)
 units,recovery_audit,c1=selective_table_recovery(old,tables,source_map)
 units,form_audit,c2=compose_form_instructions(units)
 units,inline_audit,c3=split_internal_boundaries(units,tree["document_id"])
 units,cross_audit,c4=merge_cross_page_text(units)
 changes=c1+c2+c3+c4;changed_old={x for c in changes for x in c["old_unit_ids"]}
 changes += [{"old_unit_ids":[u["unit_id"]],"new_unit_ids":[u["unit_id"]],"change_type":"UNCHANGED"} for u in old if u["unit_id"] not in changed_old]
 base=quality_report(tree,blocks,tables,units,[],a.strict,outline_roles);sem=semantic_metrics(units,tables)
 base["metrics"].update(sem);base["metrics"].update({"cross_page_table_group_count":sum(t.get("page_start")!=t.get("page_end") for t in tables),"stage2_v3_input_unit_count":len(old),"form_instruction_composed_count":len(form_audit),"inline_heading_split_count":len(inline_audit),"cross_page_text_continuation_merged_count":len(cross_audit),"selective_table_recovery_attempted_count":len(recovery_audit),"selective_table_recovery_success_count":sum(x["success"] for x in recovery_audit)})
 g19keys=["bare_form_instruction_unit_count","orphan_continuation_fragment_count","unit_contains_multiple_high_confidence_internal_heading_count","recovery_required_high_priority_content_count"]
 g19pass=all(base["metrics"][x]==0 for x in g19keys);base["gates"]=[g for g in base["gates"] if g["gate_id"]!="G18"]+[dict(gate_id="G19",name_zh="Content Unit语义完整性校验关",status_zh="通过" if g19pass else "未通过",metrics=g19keys),dict(gate_id="G18",name_zh="Content Unit总验收关",status_zh="通过" if g19pass and not base["failures"] else "未通过",metrics=[])]
 for k in g19keys:
  if base["metrics"][k] and k not in base["failures"]:base["failures"].append(k)
 passed=not base["failures"] and g19pass;base["status_zh"]="通过" if passed else "未通过";base["scope_stop"]="Stage2-v3.1 semantic coherence only; no Override/Applicability/Reference/Chunk/LLM"
 semantic_audit=[{"metric":k,"value":v} for k,v in sem.items()]
 wjl(a.output/"content_units_v3_1.jsonl",units);(a.output/"content_units_v3_1.md").write_text(markdown(units,base),encoding="utf-8");wj(a.output/"content_unit_quality_report_v3_1.json",base)
 wjl(a.output/"content_unit_change_map_v31.jsonl",changes);wjl(a.output/"semantic_coherence_audit.jsonl",semantic_audit);wjl(a.output/"form_instruction_composition_audit.jsonl",form_audit);wjl(a.output/"inline_boundary_audit.jsonl",inline_audit);wjl(a.output/"cross_page_text_continuation_audit.jsonl",cross_audit);wjl(a.output/"selective_table_recovery_audit.jsonl",recovery_audit)
 unchanged=before=={str(p.resolve()):sha(p) for p in inputs};manifest={"document_id":tree["document_id"],"pipeline":"stage2_v3_1_semantic_coherence","created_at":datetime.now(timezone.utc).isoformat(),"strict":a.strict,"mineru_rerun":False,"blanket_ocr":False,"table_group_rebuilt":False,"frozen_document_tree_modified":not unchanged,"inputs":[{"path":p,"sha256":h} for p,h in before.items()]};wj(a.output/"manifest.json",manifest)
 report=f"# Stage2-v3.1 实施报告\n\n- 输入Unit：{len(old)}\n- 最终Unit：{len(units)}\n- Form Instruction合并：{len(form_audit)}\n- Inline split：{len(inline_audit)}\n- 跨页正文合并：{len(cross_audit)}\n- Selective Table Recovery：{sum(x['success'] for x in recovery_audit)}/{len(recovery_audit)}\n- Strict：{base['status_zh']}\n- 未运行MinerU、未进行blanket OCR、未重建Table Group、未修改Document Tree。\n";(a.output/"implementation_report_stage2_v3_1.md").write_text(report,encoding="utf-8");(a.output/"run.log").write_text(f"status={base['status_zh']}\nstrict={a.strict}\nfrozen_inputs_unchanged={unchanged}\n",encoding="utf-8")
 print(json.dumps(base,ensure_ascii=False,indent=2));return 0 if passed else (2 if a.strict else 0)
if __name__=="__main__":raise SystemExit(main())
