from __future__ import annotations
def _overlap(a,b):
    if not a or not b:return False
    x1,y1,x2,y2=max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3]); return max(0,x2-x1)*max(0,y2-y1)>0
def run(blocks,native):
    pages={p["page_idx"]:p for p in native["pages"]}; table_seq=0
    confirmed_native=set()
    for candidate in blocks:
        if candidate.parser_type=="table" or (candidate.html and "<table" in candidate.html.lower()):
            for i,box in enumerate(pages[candidate.page_idx]["native_table_bboxes"]):
                if _overlap(candidate.bbox,box): confirmed_native.add((candidate.page_idx,i))
    for b in blocks:
        if not b.review_enabled: continue
        evidence=[]
        if b.parser_type=="table": evidence.append("MINERU_V2_TABLE")
        if b.html and "<table" in b.html.lower(): evidence.append("VALID_TABLE_HTML")
        native_index=next((i for i,x in enumerate(pages[b.page_idx]["native_table_bboxes"]) if _overlap(b.bbox,x)),None)
        if native_index is not None: evidence.append("PYMUPDF_TABLE_OVERLAP")
        if b.parser_type=="table" or "VALID_TABLE_HTML" in evidence or (native_index is not None and (b.page_idx,native_index) in confirmed_native):
            table_seq+=1; b.canonical_region="TABLE"; b.region_confidence=min(.98,.72+.1*len(evidence)); b.owner_type="TABLE"
            b.owner_id=f"{b.doc_id}-P{b.page_idx:04d}-NT{native_index:03d}" if native_index is not None else f"{b.doc_id}-T{table_seq:04d}"
            b.text_flow_enabled=False
        elif native_index is not None:
            b.canonical_region="UNCERTAIN"; b.region_confidence=.5; b.owner_type="UNCERTAIN"; b.owner_id=f"{b.doc_id}-U{b.page_idx:04d}-{b.reading_order:04d}"; b.text_flow_enabled=False; b.quality_flags.append("NATIVE_TABLE_ONLY_UNCERTAIN")
        elif b.parser_type=="title": b.canonical_region="TITLE"; b.region_confidence=.9; b.owner_type="TEXT"; b.owner_id="TEXT_FLOW"
        elif b.text_normalized: b.canonical_region="TEXT"; b.region_confidence=.8; b.owner_type="TEXT"; b.owner_id="TEXT_FLOW"
        else: b.canonical_region="UNCERTAIN"; b.region_confidence=.4; b.owner_type="UNCERTAIN"; b.owner_id=f"{b.doc_id}-U{b.page_idx:04d}-{b.reading_order:04d}"; b.quality_flags.append("UNKNOWN_STRUCTURE")
        b.region_evidence=evidence or ["PARSER_TEXT_SEMANTICS"]
    return blocks
