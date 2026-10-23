from __future__ import annotations
import statistics
def build(blocks,tables,units,chunks,native,adapter_stats):
    active=[b for b in blocks if b.review_enabled]; nonnoise=[b for b in blocks if b.canonical_region!="NOISE"]
    owned=[b for b in nonnoise if b.owner_type in {"TEXT","TABLE","UNCERTAIN"}]
    pages={s.pdf_page for c in chunks for s in c.source_spans}; lengths=[len(c.text_raw) for c in chunks] or [0]
    missing_spans=sum(not c.source_spans for c in chunks); missing_blocks=sum(not c.source_block_ids for c in chunks)
    return {"raw_block_count":len(blocks),"active_block_count":len(active),"noise_block_count":len(blocks)-len(active),
      "text_region_count":sum(b.canonical_region in {"TEXT","TITLE"} for b in blocks),"table_region_count":sum(b.canonical_region=="TABLE" for b in blocks),
      "uncertain_region_count":sum(b.canonical_region=="UNCERTAIN" for b in blocks),"table_count":len(tables),
      "table_v2_html_count":sum(t["source"]=="V2_HTML" for t in tables),"table_middle_fallback_count":adapter_stats.get("middle_fallback",0),
      "table_native_fallback_count":adapter_stats.get("native_fallback",0),"unresolved_table_count":sum(t["source"]=="TABLE_FRAGMENT" for t in tables),
      "clause_count":sum(u.unit_type=="CLAUSE" for u in units),"paragraph_unit_count":sum(u.unit_type=="PARAGRAPH" for u in units),
      "table_row_count":sum(u.unit_type=="TABLE_ROW" for u in units),"uncertain_unit_count":sum(u.unit_type=="UNCERTAIN_FRAGMENT" for u in units),
      "cross_page_clause_merge_count":sum(u.unit_type=="CLAUSE" and u.pdf_page_end>u.pdf_page_start for u in units),"cross_page_table_merge_count":0,
      "chunk_count":len(chunks),"chunk_length_p50":round(statistics.median(lengths),1),"chunk_length_p95":sorted(lengths)[min(len(lengths)-1,int(.95*(len(lengths)-1)))],
      "missing_page_count":len(set(range(native["page_count"]))-{p["page_idx"] for p in native["pages"]}),"invalid_page_count":sum(c.pdf_page_start<1 or c.pdf_page_end>native["page_count"] for c in chunks),
      "missing_source_span_count":missing_spans,"missing_source_block_count":missing_blocks,
      "structure_uncertain_count":sum(u.unit_type=="UNCERTAIN_FRAGMENT" or "TABLE_STRUCTURE_UNCERTAIN" in u.quality_flags for u in units),
      "owned_non_noise_blocks":len(owned),"all_non_noise_blocks":len(nonnoise),"ownership_coverage":len(owned)/len(nonnoise) if nonnoise else 1.0}
