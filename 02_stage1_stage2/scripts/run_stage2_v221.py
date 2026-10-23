from __future__ import annotations
import argparse,hashlib,json,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.document_processing.tree_consistency_v221 import build_v21_to_canonical_map,migrate_local_heading_parents,promote_numbering_gaps,recompute_depth,position
from src.document_processing.text_utils_v2 import normalize_text
def j(p):return json.loads(p.read_text(encoding="utf-8"))
def jl(p):return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x]
def wj(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding="utf-8")
def wjl(p,x):p.write_text("".join(json.dumps(r,ensure_ascii=False)+"\n" for r in x),encoding="utf-8")
def sha(p):
 h=hashlib.sha256();h.update(p.read_bytes());return h.hexdigest()
def md(tree):
 by={x["section_id"]:x for x in tree["sections"]};lines=["# DOC011 Document Tree v2.2.1",""]
 def walk(s,d=0):
  n=by[s];tag=f"[{n['section_role']}] " if n["source"]!="NATIVE_OUTLINE" else "";lines.append("  "*d+f"- {tag}{n['title']} (PDF {n['anchor']['page_idx']+1}, depth={n['depth']})")
  for c in n["children_ids"]:walk(c,d+1)
 for s in tree["body"]["root_section_ids"]:walk(s)
 return "\n".join(lines)+"\n"
def main():
 ap=argparse.ArgumentParser();ap.add_argument("--strict",action="store_true");ap.add_argument("--v22",type=Path,default=ROOT/"outputs/stage2_v2_2/DOC011");ap.add_argument("--v21",type=Path,default=ROOT/"outputs/stage2_v2/DOC011");ap.add_argument("--output",type=Path,default=ROOT/"outputs/stage2_v2_2_1/DOC011");a=ap.parse_args();a.output.mkdir(parents=True,exist_ok=True)
 req22=["document_tree_v2.json","canonical_outline_v2.json","hierarchy_change_map.jsonl","structure_quality_report_v2.json","table_groups_v2.jsonl"];req21=["canonical_outline.json"]
 if any(not (a.v22/x).exists() for x in req22) or any(not (a.v21/x).exists() for x in req21):return 3
 tree=j(a.v22/"document_tree_v2.json");sections=tree["sections"];locals0=tree["local_headings"];canon21=j(a.v21/"canonical_outline.json");quality22=j(a.v22/"structure_quality_report_v2.json")
 mapping=build_v21_to_canonical_map(canon21,sections);locals1,parent_changes=migrate_local_heading_parents(locals0,mapping,{x["section_id"] for x in sections})
 sections2,locals2,promoted,gap_audit=promote_numbering_gaps("DOC011",sections,locals1);depth_changed,depth_bad=recompute_depth(sections2)
 by={x["section_id"]:x for x in sections2};invalid=[x["local_heading_id"] for x in locals2 if x.get("canonical_parent_section_id") not in by];gap_missing=[x for x in gap_audit if not x["promoted"] and x["same_style"] and not x["content_negative_signal"] and not x["structural_section_crossed"]]
 changes=list(parent_changes)
 for p in promoted:changes.append({"entity_source_id":p["derived_from_local_heading_id"],"change_type":"NUMBERING_GAP_PROMOTION","old_role":"LOCAL_HEADING","new_role":"SUPPLEMENTARY_SECTION","old_parent_id":next(x["source_parent_section_id_v21"] for x in locals1 if x["local_heading_id"]==p["derived_from_local_heading_id"]),"new_parent_id":p["parent_id"],"reason_codes":p["parent_inference"]["evidence"]})
 roots=[x["section_id"] for x in sections2 if not x.get("parent_id")];tree2={**tree,"version":"2.2.1","body":{"root_section_ids":roots},"sections":sections2,"local_headings":locals2,"numbering_gap_promotions":promoted}
 gates=[dict(x) for x in quality22["gates"]]
 for g in gates:
  if g["gate_id"] in {"G09_HIERARCHY","G10_ACCEPTANCE"}:g["status_zh"]="通过" if not invalid and not depth_bad and not gap_missing else "未通过"
 extra=[{"gate_id":"G09A_LOCAL_PARENT","name_zh":"Local Heading父级引用一致性","status_zh":"通过" if not invalid else "未通过"},{"gate_id":"G09B_DEPTH","name_zh":"Tree Depth一致性","status_zh":"通过" if not depth_bad else "未通过"},{"gate_id":"G09C_NUMBERING_GAP","name_zh":"高置信编号缺口恢复","status_zh":"通过" if not gap_missing else "未通过"}]
 report={"doc_id":"DOC011","status_zh":"通过" if not invalid and not depth_bad and not gap_missing else "未通过","strict_requested":a.strict,"gates":gates[:-1]+extra+[gates[-1]],"metrics":{"local_heading_total_before":len(locals0),"local_heading_total_after":len(locals2),"canonical_parent_fixed_count":len(parent_changes),"local_heading_invalid_canonical_parent_count":len(invalid),"depth_recalculated_node_count":depth_changed,"tree_depth_inconsistency_count":len(depth_bad),"numbering_gap_candidate_count":len(gap_audit),"numbering_gap_promoted_count":len(promoted),"high_confidence_numbering_gap_not_promoted_count":len(gap_missing)},"numbering_gap_audit":gap_audit,"v22_regressions":quality22["regressions"],"failures":invalid+depth_bad+[x["local_heading_id"] for x in gap_missing],"scope_stop":"Document Tree canonical consistency only; no Content Unit/Chunk/LLM"}
 wj(a.output/"document_tree_v2_2_1.json",tree2);(a.output/"document_tree_v2_2_1.md").write_text(md(tree2),encoding="utf-8");wjl(a.output/"hierarchy_change_map_v2_2_1.jsonl",changes);wj(a.output/"structure_quality_report_v2_2_1.json",report)
 wj(a.output/"manifest.json",{"doc_id":"DOC011","pipeline":"stage2_v2_2_1_consistency_closeout","created_at":datetime.now(timezone.utc).isoformat(),"mineru_rerun":False,"page_registry_rebuilt":False,"source_atom_rebuilt":False,"reused_v22":[{"name":x,"sha256":sha(a.v22/x)} for x in req22],"reused_v21":[{"name":x,"sha256":sha(a.v21/x)} for x in req21],"strict":a.strict});(a.output/"run.log").write_text(f"status={report['status_zh']}\nstrict={a.strict}\n",encoding="utf-8");print(json.dumps(report,ensure_ascii=False,indent=2));return 0 if report["status_zh"]=="通过" else (2 if a.strict else 0)
if __name__=="__main__":raise SystemExit(main())
