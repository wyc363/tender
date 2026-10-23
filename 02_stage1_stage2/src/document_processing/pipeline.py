from __future__ import annotations
import hashlib,json
from pathlib import Path
from . import mineru_adapter,pdf_native_adapter,cleaner,structure_reconciler,table_recovery,table_row_builder,text_flow_builder,clause_builder,cross_page_merger,section_enricher,chunk_builder,quality_reporter
from .schemas import LogicalUnit,SourceSpan

def _dump_jsonl(path,items):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",encoding="utf-8") as f:
        for x in items:
            value=x.model_dump(mode="json") if hasattr(x,"model_dump") else x; f.write(json.dumps(value,ensure_ascii=False)+"\n")
def build_stage2_document(doc_id: str,pdf_path: Path,mineru_raw_dir: Path,output_dir: Path,config: dict,section_map_path: Path|None=None):
    output_dir.mkdir(parents=True,exist_ok=True)
    blocks,adapter_stats=mineru_adapter.load(doc_id,mineru_raw_dir); native=pdf_native_adapter.load(pdf_path,blocks)
    cleaned=cleaner.run([b.model_copy(deep=True) for b in blocks],native,config["cleaner"])
    region=structure_reconciler.run([b.model_copy(deep=True) for b in cleaned],native); block_map={b.block_id:b for b in region}
    tables=table_recovery.run(region,native); table_units=table_row_builder.run(tables,block_map)
    text_units=clause_builder.run(text_flow_builder.run(region))
    uncertain=[]
    for b in region:
        if b.canonical_region=="UNCERTAIN" and b.review_enabled:
            uncertain.append(LogicalUnit(unit_id=b.owner_id,doc_id=doc_id,unit_type="UNCERTAIN_FRAGMENT",text_raw=b.text_raw,text_normalized=b.text_normalized,
              pdf_page_start=b.pdf_page,pdf_page_end=b.pdf_page,source_block_ids=[b.block_id],source_spans=[SourceSpan(pdf_page=b.pdf_page,page_idx=b.page_idx,bbox=b.bbox,source_file=b.source_file,source_block_id=b.block_id)],structure_confidence=.4,quality_flags=b.quality_flags))
    units=cross_page_merger.run(table_units+text_units+uncertain)
    sm=json.loads(section_map_path.read_text(encoding="utf-8")) if section_map_path and section_map_path.exists() else {}
    units=section_enricher.run(units,sm); chunks=chunk_builder.run(units,pdf_path.name,config["chunking"])
    report=quality_reporter.build(region,tables,units,chunks,native,adapter_stats)
    manifest={"doc_id":doc_id,"file_name":pdf_path.name,"sha256":hashlib.sha256(pdf_path.read_bytes()).hexdigest(),"total_pages":native["page_count"],
              "parse_mode":"FULL","original_page_offset":0,"mineru_version":"2.6.4-output","pipeline_version":"stage2-v1","mineru_status":"SUCCESS","raw_dir":str(mineru_raw_dir)}
    for name,items in [("unified_blocks",blocks),("cleaned_blocks",cleaned),("region_blocks",region),("tables",tables),("logical_units",units),("chunks",chunks)]: _dump_jsonl(output_dir/f"{name}.jsonl",items)
    _dump_jsonl(output_dir/"page_quality.jsonl",native["pages"])
    (output_dir/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    (output_dir/"quality_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return {"manifest":manifest,"quality_report":report}
