from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"outputs/stage2"
def loadl(p): return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x]
def main():
    result={"purpose":"human_gate_stratified_sample","documents":[]}
    for doc in ("DOC011","DOC012"):
        chunks=loadl(OUT/doc/"chunks.jsonl"); blocks=loadl(OUT/doc/"region_blocks.jsonl")
        chosen=[]
        def take(label,pred,n):
            for c in filter(pred,chunks):
                if c["chunk_id"] not in {x["chunk_id"] for x in chosen} and sum(x["sample_group"]==label for x in chosen)<n:
                    chosen.append({"sample_group":label,"chunk_id":c["chunk_id"],"chunk_type":c["chunk_type"],"pages":[c["pdf_page_start"],c["pdf_page_end"]],"text_preview":c["text_raw"][:300],"source_block_ids":c["source_block_ids"][:5],"quality_flags":c["quality_flags"]})
        take("CLAUSE",lambda c:c["chunk_type"]=="CLAUSE",7); take("TABLE_ROW",lambda c:c["chunk_type"]=="TABLE_ROW",7)
        take("CROSS_PAGE",lambda c:c["pdf_page_end"]>c["pdf_page_start"],4); take("LONG",lambda c:len(c["text_raw"])>800,3)
        take("SHORT",lambda c:0<len(c["text_raw"])<80,3); take("UNCERTAIN",lambda c:c["chunk_type"]=="UNCERTAIN_FRAGMENT",2)
        corrections=[{"block_id":b["block_id"],"page":b["pdf_page"],"parser_type":b["parser_type"],"canonical_region":b["canonical_region"],"evidence":b["region_evidence"],"text_preview":b["text_raw"][:200]}
                     for b in blocks if b["parser_type"] not in {"table"} and b["canonical_region"]=="TABLE"][:5]
        result["documents"].append({"doc_id":doc,"sample_count":len(chosen),"samples":chosen,"mineru_text_to_table_examples":corrections,
                                    "mineru_table_to_text_examples":[],"uncertain_examples":[x for x in chosen if x["sample_group"]=="UNCERTAIN"]})
    (OUT/"stage2_step1_human_qa.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({d["doc_id"]:d["sample_count"] for d in result["documents"]},ensure_ascii=False)); return 0
if __name__=="__main__": raise SystemExit(main())
