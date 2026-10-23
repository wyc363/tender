from __future__ import annotations

import argparse,hashlib,json,sys
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.document_processing.front_matter_v32 import build_front_matter
from src.document_processing.table_text_consumption_v32 import build_logical_rows,consumption_audit
from src.document_processing.canonical_event_stream_v32 import build_events
from src.document_processing.readable_renderer_v32 import render
from src.document_processing.readback_mismatch_tracer_v32 import trace_mismatches
from src.document_processing.readback_quality_v32 import quality

def rj(p):return json.loads(p.read_text(encoding="utf-8-sig"))
def rjl(p):return [json.loads(x) for x in p.read_text(encoding="utf-8-sig").splitlines() if x]
def wj(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
def wjl(p,x):p.write_text("".join(json.dumps(y,ensure_ascii=False)+"\n" for y in x),encoding="utf-8")
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--document-id",required=True);ap.add_argument("--strict",action="store_true");a=ap.parse_args();d=a.document_id
    paths={"tree":ROOT/f"outputs/stage2_v2_2_2/{d}/document_tree_v2_2_2.json","units":ROOT/f"outputs/stage2_v3_1_content_units/{d}/content_units_v3_1.jsonl","blocks":ROOT/f"outputs/stage2_v2/{d}/canonical_blocks.jsonl","tables":ROOT/f"outputs/stage2_v2_2/{d}/table_groups_v2.jsonl","source":ROOT/f"outputs/stage2_v2/{d}/source_representation_map.jsonl","row_audit":ROOT/f"outputs/stage2_v3_content_units/{d}/table_row_group_audit.jsonl","old_quality":ROOT/f"outputs/stage2_v3_1_content_units/{d}/content_unit_quality_report_v3_1.json"}
    before={k:sha(v) for k,v in paths.items()};tree=rj(paths["tree"]);units=rjl(paths["units"]);blocks=rjl(paths["blocks"]);source=rjl(paths["source"]);row_audit=rjl(paths["row_audit"])
    first=min(tuple(s["canonical_position"]) for s in tree["sections"] if s.get("parent_id") is None);front=build_front_matter(blocks,first);logical=build_logical_rows(units);ta,tm=consumption_audit(units,row_audit,logical)
    pdf=None
    try:
        import pymupdf
        source_file=next(Path(x["source_file"]) for x in source if x.get("source_file") and "_middle.json" in x["source_file"])
        origins=[p for p in source_file.parent.glob("*_origin.pdf") if not p.name.startswith("~$") and p.stat().st_size>1024]
        origin=max(origins,key=lambda p:p.stat().st_size);pdf=pymupdf.open(origin)
    except (ImportError,StopIteration,FileNotFoundError,ValueError):pass
    events=build_events(tree,units,front,logical,pdf);text,rendered=render(events,d);traces=trace_mismatches(tree,units,front,events);report=quality(d,events,rendered,front,tm,traces,rj(paths["old_quality"]));out=ROOT/f"outputs/stage2_v3_2_readback/{d}";out.mkdir(parents=True,exist_ok=True)
    wjl(out/"canonical_content_events_v3_2.jsonl",events);wjl(out/"document_front_matter_v3_2.jsonl",front);wjl(out/"table_text_consumption_audit_v3_2.jsonl",ta);wjl(out/"readback_mismatch_trace_v3_2.jsonl",traces);wj(out/"readback_quality_report_v3_2.json",report);(out/f"{d}_readable_v3_2.md").write_text(text,encoding="utf-8")
    after={k:sha(v) for k,v in paths.items()};manifest={"document_id":d,"pipeline":"stage2_v3_2_readback","created_at":datetime.now(timezone.utc).isoformat(),"strict_requested":a.strict,"strict_pass":report["strict_pass"],"mineru_rerun":False,"blanket_ocr":False,"inputs":[{"path":str(paths[k]),"sha256":v} for k,v in before.items()],"frozen_inputs_unchanged":before==after};wj(out/"manifest.json",manifest)
    summary=f"# Stage2-v3.2 Implementation Report\n\n- strict: {'PASS' if report['strict_pass'] else 'FAIL'}\n- Frozen inputs unchanged: {before==after}\n- MinerU rerun: false\n- Blanket OCR: false\n- Events: {len(events)}\n- Traces: {len(traces)}\n";(out/"implementation_report_stage2_v3_2.md").write_text(summary,encoding="utf-8");(out/"run.log").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"output":str(out),"strict_pass":report["strict_pass"],"metrics":report["metrics"],"frozen_unchanged":before==after},ensure_ascii=False,indent=2));return 0 if (report["strict_pass"] or not a.strict) else 2
if __name__=="__main__":raise SystemExit(main())
