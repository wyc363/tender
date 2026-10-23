from __future__ import annotations
import argparse,hashlib,json,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.document_processing.content_units_v3 import build_text_units,build_table_units,quality_report

def j(p): return json.loads(p.read_text(encoding="utf-8"))
def jl(p): return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
def wj(p,x): p.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding="utf-8")
def wjl(p,x): p.write_text("".join(json.dumps(y,ensure_ascii=False)+"\n" for y in x),encoding="utf-8")
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

def md(units,report):
    lines=["# DOC011 Stage2-v3 Content Units","",f"- Status: {report['status_zh']}",f"- Units: {len(units)}",""]
    current=None
    for u in units:
        if u["section_id"]!=current:
            current=u["section_id"]; lines += [f"## {' / '.join(u['section_path'])}",""]
        lines += [f"### {u['unit_id']} [{u['unit_type']}] PDF {u['page_start']+1}-{u['page_end']+1}","",u["text"],""]
    return "\n".join(lines)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--strict",action="store_true")
    ap.add_argument("--tree",type=Path,default=ROOT/"outputs/stage2_v2_2_2/DOC011/document_tree_v2_2_2.json")
    ap.add_argument("--blocks",type=Path,default=ROOT/"outputs/stage2_v2/DOC011/canonical_blocks.jsonl")
    ap.add_argument("--tables",type=Path,default=ROOT/"outputs/stage2_v2_2/DOC011/table_groups_v2.jsonl")
    ap.add_argument("--pages",type=Path,default=ROOT/"outputs/stage2_v2/DOC011/page_registry.jsonl")
    ap.add_argument("--source-map",type=Path,default=ROOT/"outputs/stage2_v2/DOC011/source_representation_map.jsonl")
    ap.add_argument("--outline-roles",type=Path,default=ROOT/"outputs/stage2_v2_2/DOC011/outline_roles_v2.json")
    ap.add_argument("--output",type=Path,default=ROOT/"outputs/stage2_v3_content_units/DOC011")
    a=ap.parse_args(); inputs=[a.tree,a.blocks,a.tables,a.pages,a.source_map,a.outline_roles]
    if any(not p.exists() for p in inputs): print("missing input",file=sys.stderr); return 3
    before={str(p.resolve()):sha(p) for p in inputs}; a.output.mkdir(parents=True,exist_ok=True)
    tree=j(a.tree); blocks=jl(a.blocks); tables=jl(a.tables); source_map=jl(a.source_map); outline_roles=j(a.outline_roles)
    text_units,clause_audit,list_audit,form_audit=build_text_units(tree,blocks,outline_roles)
    table_units,row_audit,cross_audit=build_table_units(tree,tables,source_map,len(text_units)); units=text_units+table_units
    report=quality_report(tree,blocks,tables,units,cross_audit,a.strict,outline_roles)
    wjl(a.output/"content_units.jsonl",units); (a.output/"content_units.md").write_text(md(units,report),encoding="utf-8")
    wj(a.output/"content_unit_quality_report.json",report); wjl(a.output/"clause_annotation_audit.jsonl",clause_audit)
    wjl(a.output/"list_group_audit.jsonl",list_audit); wjl(a.output/"table_row_group_audit.jsonl",row_audit)
    wjl(a.output/"cross_page_table_audit.jsonl",cross_audit); wjl(a.output/"form_classification_audit.jsonl",form_audit)
    after={str(p.resolve()):sha(p) for p in inputs}; frozen_unchanged=before==after
    manifest={"document_id":tree["document_id"],"pipeline":"stage2_v3_content_unit_reconstruction","created_at":datetime.now(timezone.utc).isoformat(),"strict":a.strict,"mineru_rerun":False,"frozen_document_tree_modified":not frozen_unchanged,"inputs":[{"path":k,"sha256":v} for k,v in before.items()]}
    wj(a.output/"manifest.json",manifest)
    implementation=f"""# Stage2-v3 实施报告\n\n- MinerU重新运行：否\n- Frozen Document Tree修改：否\n- Content Unit：{len(units)}\n- Table Group：{len(tables)}\n- Strict：{report['status_zh']}\n- STOP：未进入Override、Applicability、Reference、Chunk或LLM。\n"""
    (a.output/"implementation_report_stage2_v3.md").write_text(implementation,encoding="utf-8")
    (a.output/"run.log").write_text(f"status={report['status_zh']}\nstrict={a.strict}\nfrozen_tree_unchanged={frozen_unchanged}\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2)); return 0 if report["status_zh"]=="通过" else (2 if a.strict else 0)
if __name__=="__main__": raise SystemExit(main())
