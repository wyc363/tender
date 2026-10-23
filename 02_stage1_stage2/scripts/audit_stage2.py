from __future__ import annotations
import argparse,json
from pathlib import Path
def loadl(path): return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
def main():
    p=argparse.ArgumentParser();p.add_argument("--root",type=Path,required=True);a=p.parse_args(); docs=[]; failures=[]; ids=set()
    for d in sorted(x for x in a.root.iterdir() if x.is_dir()):
        q=json.loads((d/"quality_report.json").read_text(encoding="utf-8")); chunks=loadl(d/"chunks.jsonl"); blocks={b["block_id"] for b in loadl(d/"region_blocks.jsonl")}
        orphan=sum(any(s not in blocks for s in c["source_block_ids"]) for c in chunks); dup=0
        for c in chunks:
            if c["chunk_id"] in ids: dup+=1
            ids.add(c["chunk_id"])
        checks={"duplicate_chunk_id":dup,"invalid_pdf_page":q["invalid_page_count"],"missing_source_block":q["missing_source_block_count"],"missing_source_span":q["missing_source_span_count"],"orphan_source_reference":orphan,"ownership_coverage":q["ownership_coverage"]}
        if any(checks[k] for k in checks if k!="ownership_coverage") or checks["ownership_coverage"]!=1.0: failures.append({"doc_id":d.name,"checks":checks})
        docs.append({"doc_id":d.name,"checks":checks})
    result={"stage":"2-step1","status":"PASS" if len(docs)==2 and not failures else "FAIL","documents":docs,"failures":failures}
    (a.root/"stage2_step1_audit.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8");print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result["status"]=="PASS" else 1
if __name__=="__main__": raise SystemExit(main())
