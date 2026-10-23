from __future__ import annotations
import argparse, hashlib, json, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from src.document_processing.hierarchy_v22 import normalize_outline_roles_v2,infer_supplementary_sections,build_unified_sections,assign_ranges_and_tables,build_tree,pos
from src.document_processing.text_utils_v2 import normalize_text

def loadj(p):return json.loads(p.read_text(encoding="utf-8"))
def loadjl(p):return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
def wj(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding="utf-8")
def wjl(p,x):p.write_text("".join(json.dumps(r,ensure_ascii=False)+"\n" for r in x),encoding="utf-8")
def sha256(p):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):h.update(chunk)
    return h.hexdigest()
def tree_md(tree):
    by={x["section_id"]:x for x in tree["sections"]}; lines=["# DOC011 Document Tree v2.2",""]
    def walk(sid,d=0):
        n=by[sid]; tag=f"[{n['section_role']}] " if n["source"]!="NATIVE_OUTLINE" else ""
        lines.append("  "*d+f"- {tag}{n['title']} (PDF {n['anchor']['page_idx']+1}, {sid})")
        for c in n["children_ids"]:walk(c,d+1)
    for sid in tree["body"]["root_section_ids"]:walk(sid)
    return "\n".join(lines)+"\n"
def find(nodes,text,after=0):
    q=normalize_text(text); return [x for x in nodes if q in normalize_text(x["title"]) and x["anchor"]["page_idx"]>=after]
def main():
    ap=argparse.ArgumentParser();ap.add_argument("--strict",action="store_true");ap.add_argument("--input",type=Path,default=ROOT/"outputs/stage2_v2/DOC011");ap.add_argument("--output",type=Path,default=ROOT/"outputs/stage2_v2_2/DOC011");a=ap.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    required=["page_registry.jsonl","source_representation_map.jsonl","raw_outline.json","outline_anchor_validation.json","outline_roles.json","canonical_outline.json","canonical_blocks.jsonl","table_groups.jsonl","supplementary_heading_candidates.jsonl","structure_quality_report.json"]
    missing=[x for x in required if not (a.input/x).exists()]
    if missing: print("missing",missing);return 3
    pages=loadjl(a.input/"page_registry.jsonl");sources=loadjl(a.input/"source_representation_map.jsonl");raw=loadj(a.input/"raw_outline.json");anchors=loadj(a.input/"outline_anchor_validation.json");baseline=loadj(a.input/"structure_quality_report.json")
    roles1=loadj(a.input/"outline_roles.json");canon1=loadj(a.input/"canonical_outline.json");blocks=loadjl(a.input/"canonical_blocks.jsonl");tables1=loadjl(a.input/"table_groups.jsonl");candidates=loadjl(a.input/"supplementary_heading_candidates.jsonl")
    roles2=normalize_outline_roles_v2(roles1);supp=infer_supplementary_sections("DOC011",candidates,roles2,anchors,canon1,tables1,sources)
    nodes,reparents=build_unified_sections("DOC011",roles2,anchors,supp);tables2=assign_ranges_and_tables(nodes,tables1,sources,pages);tree=build_tree("DOC011",nodes,blocks,candidates)
    old_title={x["section_id"]:x["title_raw"] for x in canon1};new_title={x["section_id"]:x["title"] for x in nodes}
    for g in tables2:
        g["old_parent_title"]=old_title.get(g.get("old_parent_id"));g["new_parent_title"]=new_title.get((g.get("canonical_target_ids") or [None])[0])
        g["parent_changed_semantic"]=normalize_text(g.get("old_parent_title") or "")!=normalize_text(g.get("new_parent_title") or "")
    # mappings
    v1sid={x["source_outline_id"]:x for x in canon1}; newbyoid={x.get("derived_from_outline_id"):x for x in nodes if x.get("derived_from_outline_id")}
    changes=[]
    for r in roles2:
        if r["outline_role"]!=r["outline_role_v1"]:
            old=v1sid.get(r["outline_id"]);changes.append({"entity_source_id":r["outline_id"],"old_role":r["outline_role_v1"],"new_role":r["outline_role"],"old_parent_id":old.get("parent_id") if old else None,"new_parent_id":None,"reason_codes":r["role_change_reason_codes"]})
    for s in supp: changes.append({"entity_source_id":s.get("derived_from_local_heading_id") or s.get("derived_from_table_group_id"),"old_role":"LOCAL_HEADING" if s.get("derived_from_local_heading_id") else "TABLE_EVIDENCE","new_role":s["section_role"],"old_parent_id":s["parent_inference"].get("selected_parent_id"),"new_parent_id":next((n["parent_id"] for n in nodes if n["section_id"]==s["supplementary_section_id"]),None),"reason_codes":s["parent_inference"]["evidence"]})
    for sid,old,new,reason in reparents:changes.append({"entity_source_id":sid,"old_role":"SECTION","new_role":"SECTION","old_parent_id":old,"new_parent_id":new,"reason_codes":[reason]})
    def parent_title(n): return next((x["title"] for x in nodes if x["section_id"]==n.get("parent_id")),None)
    regress=[]
    def rel(child,parent,after=0):
        cs=find(nodes,child,after); ok=bool(cs and normalize_text(parent) in normalize_text(parent_title(cs[0]) or ""));regress.append({"case":f"{child} -> {parent}","passed":ok,"actual_parent":parent_title(cs[0]) if cs else None});return ok
    # Explicit DOC011 regression fixtures are allowed by the plan; production inference above is generic.
    for child,parent,after in [("（一）总则","一、投标须知",0),("1.工程说明","（一）总则",0),("6.知识产权","（一）总则",0),("（二）招标文件","一、投标须知",0),("7.招标文件的组成","（二）招标文件",0),("10.招标文件异议处理","（二）招标文件",0),("（三）投标文件的编制","一、投标须知",0),("（四）投标文件的递交","一、投标须知",0),("（五）开标","一、投标须知",0),("（六）评标","一、投标须知",0),("（七）合同授予","一、投标须知",0),("第2节通用合同条款","第2节合同条款",0),("第3节设计专用合同条款","第2节合同条款",0),("附件三保函、保险的格式","二、投标须知和附件",0)]: rel(child,parent,after)
    content_titles=["2.签约合同价为","2.支付方式：详见","（本项目不包含勘察）"]
    content_errors=[]
    for q in content_titles:
        rr=[x for x in roles2 if normalize_text(q) in normalize_text(x["raw_title"])]; ok=bool(rr and rr[0]["outline_role"] not in {"STRUCTURAL_SECTION","TOPIC_SECTION","ATTACHMENT_SECTION"});regress.append({"case":q+" NOT SECTION","passed":ok,"actual_role":rr[0]["outline_role"] if rr else None});
        if not ok:content_errors.append(q)
    tg21=next((x for x in tables2 if x["page_start"]==166 and x["page_end"]==171),None); tg21parent=next((n["title"] for n in nodes if tg21 and n["section_id"] in tg21["canonical_target_ids"]),None)
    regress.append({"case":"PDF167-172 table parent","passed":bool(tg21 and "通用本文件内容补正表" in normalize_text(tg21parent or "") and len(tg21["physical_fragment_source_atom_ids"])==6),"actual_parent":tg21parent})
    tg_cross=[x for x in tables2 if x["crosses_structural_section_boundary"]]; missing_parent=[x for x in tables2 if not x["canonical_target_ids"]]
    image=[x for x in tables2 if x["requires_image_content_recovery"]]
    pdf124=[x for x in tables2 if x["page_start"]<=123<=x["page_end"]]
    regress.append({"case":"PDF124 image recovery","passed":bool(pdf124 and pdf124[0]["requires_image_content_recovery"]),"candidate":pdf124[0]["table_group_id"] if pdf124 else None})
    downgraded=[x for x in roles2 if x["outline_role_v1"] in {"STRUCTURAL_SECTION","TOPIC_SECTION","ATTACHMENT_SECTION"} and x["outline_role"] not in {"STRUCTURAL_SECTION","TOPIC_SECTION","ATTACHMENT_SECTION"}]
    supp_orphans=[x for x in nodes if x["source"]!="NATIVE_OUTLINE" and not x["parent_id"] and not x["quality_flags"]]
    cycles=[]
    by={x["section_id"]:x for x in nodes}
    for n in nodes:
        seen=set();p=n.get("parent_id")
        while p:
            if p in seen:cycles.append(n["section_id"]);break
            seen.add(p);p=by.get(p,{}).get("parent_id")
    missing_node_parents=[x["section_id"] for x in nodes if x.get("parent_id") and x["parent_id"] not in by]
    attached={bid for n in nodes for bid in n.get("content_block_ids",[])}
    first_body=min((pos(x) for x in nodes),default=(0,0))
    unattached=[b["canonical_block_id"] for b in blocks if b.get("review_enabled") and not b.get("front_matter_role") and (b["page_idx"],(b.get("bbox_pdf") or [0,0])[1])>=first_body and b["canonical_block_id"] not in attached]
    source_atoms=[x for x in sources if x.get("source_atom_id")]; source_ids=[x["source_atom_id"] for x in source_atoms]
    unaccounted=[x["source_atom_id"] for x in source_atoms if x.get("representation_role")!="DISCARDED_SOURCE_ATOM" and not x.get("canonical_target_ids")]
    duplicate_primary=len(source_ids)-len(set(source_ids))
    required_texts=["附件三保函、保险的格式","福建省建筑工程设计招标综合评估法评分标准","本项目不包含勘察","另册提供"]
    block_text=normalize_text("".join(b.get("text_canonical","") for b in blocks)); completeness=all(normalize_text(x) in block_text for x in required_texts)
    relerr=[x for x in regress if not x["passed"]]
    gates=[
      ("G01_PHYSICAL","第一关：物理页面与坐标校验关",len(pages)==baseline["page_count"] and not [x for x in sources if x.get("representation_role")=="PRIMARY_SOURCE_ATOM" and not x.get("bbox_pdf")]),
      ("G02_SOURCE","第二关：源数据映射与去重校验关",len(source_atoms)==baseline["source_atom_gate"]["total"] and not unaccounted and duplicate_primary==0),
      ("G03_COMPLETENESS","第三关：原文完整性校验关",completeness),("G04_ANCHOR","第四关：目录锚点定位校验关",all(x["status"]=="VERIFIED" for x in anchors if x["outline_role"] in {"STRUCTURAL_SECTION","TOPIC_SECTION","ATTACHMENT_SECTION"})),
      ("G05_ROLE","第五关：目录节点角色校验关",not content_errors),("G06_SUPPLEMENTARY","第六关：缺失标题补充与层级校验关",not supp_orphans and not [x for x in relerr if "table" not in x["case"] and "image" not in x["case"]]),
      ("G07_TABLE","第七关：表格章节归属校验关",not missing_parent and not tg_cross and bool(tg21)),("G08_SPECIAL","第八关：特殊内容保留校验关",all(x["passed"] for x in regress[-2:])),
      ("G09_HIERARCHY","第九关：文档层级结构校验关",not cycles and not missing_node_parents and not unattached and not relerr and not supp_orphans and not missing_parent and not tg_cross),]
    gates.append(("G10_ACCEPTANCE","第十关：文档结构总验收关",all(x[2] for x in gates[4:])))
    report={"doc_id":"DOC011","status_zh":"通过" if gates[-1][2] else "未通过","strict_requested":a.strict,"gates":[{"gate_id":i,"name_zh":n,"status_zh":"通过" if ok else "未通过"} for i,n,ok in gates],
      "metrics":{"page_count":len(pages),"source_atom_count":len(source_atoms),"source_atom_unaccounted_count":len(unaccounted),"duplicate_primary_count":duplicate_primary,"outline_role_changes":sum(x["outline_role"]!=x["outline_role_v1"] for x in roles2),"section_to_content_downgraded":len(downgraded),"supplementary_section_count":sum(x["section_role"]=="SUPPLEMENTARY_SECTION" for x in supp),"supplementary_attachment_count":sum(x["section_role"]=="SUPPLEMENTARY_ATTACHMENT_SECTION" for x in supp),"supplementary_section_orphan_count":len(supp_orphans),"parent_reparent_count":len(reparents),"tree_cycle_count":len(cycles),"tree_node_parent_missing_count":len(missing_node_parents),"unattached_active_body_block_count":len(unattached),"known_parent_relation_error_count":len(relerr),"table_parent_changed_count":sum(x["parent_changed_semantic"] for x in tables2),"table_group_id_remapped_count":sum(x["parent_changed"] for x in tables2),"table_group_split_count":len(tables2)-len(tables1),"table_group_parent_missing_count":len(missing_parent),"cross_structural_table_group_count":len(tg_cross),"image_recovery_candidate_count":len(image),"image_recovery_candidate_ratio":len(image)/len(tables2),"image_recovery_candidate_overclassification_count":sum(x["requires_image_content_recovery"] and not (x["page_start"]<=123<=x["page_end"]) for x in tables2)},
      "regressions":regress,"failures":[x["name_zh"] for x in report_gates(gates) if x["status_zh"]=="未通过"],"scope_stop":"仅文档层级结构纠正；未生成Content Unit/Chunk；未调用LLM"}
    wj(a.output/"manifest.json",{"doc_id":"DOC011","pipeline":"stage2_v2_2_hierarchy_correction","created_at":datetime.now(timezone.utc).isoformat(),"reused_input_dir":str(a.input),"reused_files":[{"name":x,"sha256":sha256(a.input/x)} for x in required],"mineru_rerun":False,"page_registry_rebuilt":False,"source_atom_reparsed":False,"strict":a.strict})
    wj(a.output/"outline_roles_v2.json",roles2);wjl(a.output/"supplementary_sections.jsonl",supp);wjl(a.output/"hierarchy_change_map.jsonl",changes);wj(a.output/"canonical_outline_v2.json",nodes);wjl(a.output/"table_groups_v2.jsonl",tables2);wj(a.output/"document_tree_v2.json",tree);(a.output/"document_tree_v2.md").write_text(tree_md(tree),encoding="utf-8");wj(a.output/"structure_quality_report_v2.json",report)
    (a.output/"run.log").write_text(f"status={report['status_zh']}\nstrict={a.strict}\n",encoding="utf-8");print(json.dumps(report,ensure_ascii=False,indent=2));return 0 if gates[-1][2] else (2 if a.strict else 0)
def report_gates(g):return [{"gate_id":i,"name_zh":n,"status_zh":"通过" if ok else "未通过"} for i,n,ok in g]
if __name__=="__main__":raise SystemExit(main())
