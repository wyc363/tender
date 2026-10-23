from __future__ import annotations
import argparse,hashlib,json,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.document_processing.section_intervals_v222 import recompute_section_ranges,reassign_content_ownership,reverify_table_ownership,interval_diagnostics
def j(p):return json.loads(p.read_text(encoding="utf-8"))
def jl(p):return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x]
def wj(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding="utf-8")
def wjl(p,x):p.write_text("".join(json.dumps(r,ensure_ascii=False)+"\n" for r in x),encoding="utf-8")
def sha(p):h=hashlib.sha256();h.update(p.read_bytes());return h.hexdigest()
def tree_md(tree):
 by={x["section_id"]:x for x in tree["sections"]};lines=["# DOC011 Document Tree v2.2.2",""]
 def walk(s,d=0):
  n=by[s];tag=f"[{n['section_role']}] " if n["source"]!="NATIVE_OUTLINE" else "";r=n["range"];lines.append("  "*d+f"- {tag}{n['title']} (PDF {n['anchor']['page_idx']+1}, blocks={len(n['content_block_ids'])}, tables={len(n['table_group_ids'])}, range={r['start']}→{r['end_exclusive']})")
  for c in n["children_ids"]:walk(c,d+1)
 for s in tree["body"]["root_section_ids"]:walk(s)
 return "\n".join(lines)+"\n"
def main():
 ap=argparse.ArgumentParser();ap.add_argument("--strict",action="store_true");ap.add_argument("--v221",type=Path,default=ROOT/"outputs/stage2_v2_2_1/DOC011");ap.add_argument("--v22",type=Path,default=ROOT/"outputs/stage2_v2_2/DOC011");ap.add_argument("--v21",type=Path,default=ROOT/"outputs/stage2_v2/DOC011");ap.add_argument("--output",type=Path,default=ROOT/"outputs/stage2_v2_2_2/DOC011");a=ap.parse_args();a.output.mkdir(parents=True,exist_ok=True)
 req=[a.v221/"document_tree_v2_2_1.json",a.v221/"structure_quality_report_v2_2_1.json",a.v22/"table_groups_v2.jsonl",a.v21/"canonical_blocks.jsonl",a.v21/"page_registry.jsonl"]
 if any(not p.exists() for p in req):return 3
 oldtree=j(req[0]);qprev=j(req[1]);tables=jl(req[2]);blocks=jl(req[3]);pages=jl(req[4])
 sections,events,interval_audit=recompute_section_ranges(oldtree["sections"],len(pages));ownership,multiple,mismatch,unowned=reassign_content_ownership(sections,blocks,oldtree["sections"]);tables2,table_audit,table_mismatch=reverify_table_ownership(sections,tables);intermediate,missing_range,missing_fields=interval_diagnostics(sections)
 changed_owner=sum(x["owner_changed"] for x in ownership);changed_range=sum(x["range_changed"] for x in interval_audit)
 tree={**oldtree,"version":"2.2.2","sections":sections,"section_event_stream":events}
 metrics={"formal_section_count":len(sections),"section_range_recomputed_count":len(sections),"section_range_changed_count":changed_range,"promoted_section_missing_range_count":len(missing_range),"promoted_section_missing_content_field_count":len(missing_fields),"sibling_interval_contains_intermediate_section_anchor_count":len(intermediate),"active_content_block_count":len(ownership),"content_block_owner_reassigned_count":changed_owner,"content_block_owner_interval_mismatch_count":len(mismatch),"content_block_multiple_section_owner_count":multiple,"active_content_block_unowned_count":len(unowned),"table_group_parent_reverified_count":len(tables2),"table_group_parent_changed_count":sum(x["parent_changed"] for x in table_audit),"table_group_parent_interval_mismatch_count":len(table_mismatch)}
 zero_keys=["promoted_section_missing_range_count","promoted_section_missing_content_field_count","sibling_interval_contains_intermediate_section_anchor_count","content_block_owner_interval_mismatch_count","content_block_multiple_section_owner_count","active_content_block_unowned_count","table_group_parent_interval_mismatch_count"]
 passed=all(metrics[k]==0 for k in zero_keys) and qprev["status_zh"]=="通过"
 gates=[dict(x) for x in qprev["gates"]]
 for g in gates:
  if g["gate_id"] in {"G09_HIERARCHY","G10_ACCEPTANCE"}:g["status_zh"]="通过" if passed else "未通过"
 extras=[("G09D_PROMOTED_RANGE","新提升章节区间完整性","promoted_section_missing_range_count"),("G09E_PROMOTED_FIELDS","新提升章节字段完整性","promoted_section_missing_content_field_count"),("G09F_SIBLING_INTERVAL","Sibling区间一致性","sibling_interval_contains_intermediate_section_anchor_count"),("G09G_CONTENT_INTERVAL","正文归属与区间一致性","content_block_owner_interval_mismatch_count"),("G09H_CONTENT_UNIQUE","正文唯一归属","content_block_multiple_section_owner_count"),("G09I_TABLE_INTERVAL","表格父章节与区间一致性","table_group_parent_interval_mismatch_count")]
 extra_gates=[{"gate_id":i,"name_zh":n,"status_zh":"通过" if metrics[k]==0 else "未通过"} for i,n,k in extras]
 report={"doc_id":"DOC011","status_zh":"通过" if passed else "未通过","strict_requested":a.strict,"gates":gates[:-1]+extra_gates+[gates[-1]],"metrics":metrics,"previous_regressions":qprev.get("v22_regressions",[]),"previous_consistency_metrics":qprev["metrics"],"failures":[k for k in zero_keys if metrics[k]],"scope_stop":"Section intervals and content ownership only; no Content Unit/Chunk/LLM"}
 wj(a.output/"document_tree_v2_2_2.json",tree);(a.output/"document_tree_v2_2_2.md").write_text(tree_md(tree),encoding="utf-8");wj(a.output/"structure_quality_report_v2_2_2.json",report);wjl(a.output/"section_interval_audit.jsonl",interval_audit);wjl(a.output/"content_ownership_audit.jsonl",ownership);wjl(a.output/"table_ownership_audit.jsonl",table_audit)
 wj(a.output/"manifest.json",{"doc_id":"DOC011","pipeline":"stage2_v2_2_2_interval_ownership_closeout","created_at":datetime.now(timezone.utc).isoformat(),"mineru_rerun":False,"page_registry_rebuilt":False,"source_atom_rebuilt":False,"canonical_block_rebuilt":False,"table_group_rebuilt":False,"reused_inputs":[{"path":str(p),"sha256":sha(p)} for p in req],"strict":a.strict});(a.output/"run.log").write_text(f"status={report['status_zh']}\nstrict={a.strict}\n",encoding="utf-8");print(json.dumps(report,ensure_ascii=False,indent=2));return 0 if passed else (2 if a.strict else 0)
if __name__=="__main__":raise SystemExit(main())
