from __future__ import annotations
import re
from lxml import html as lh

def run(blocks,native):
    tables=[]; groups={}
    for b in blocks:
        if b.canonical_region=="TABLE": groups.setdefault(b.owner_id,[]).append(b)
    for owner_id, group in groups.items():
        b=next((x for x in group if x.html and "<table" in x.html.lower()),group[0])
        rows=[]; source="UNRESOLVED"
        if b.html:
            try:
                tree=lh.fromstring(b.html)
                rows=[[re.sub(r"\s+"," ","".join(c.itertext())).strip() for c in tr.xpath("./th|./td")] for tr in tree.xpath(".//tr")]
                rows=[r for r in rows if any(r)]; source="V2_HTML"
            except Exception: pass
        flags=[]
        if not rows:
            rows=[[x.text_normalized] for x in group if x.text_normalized]
            flags.append("TABLE_STRUCTURE_UNCERTAIN"); source="TABLE_FRAGMENT"
        source_ids=[sid for x in group for sid in x.source_block_ids]
        tables.append({"table_id":owner_id,"doc_id":b.doc_id,"page_idx_start":b.page_idx,"page_idx_end":b.page_idx,"bbox":b.bbox,
                       "rows":rows,"source":source,"source_block_ids":source_ids,"quality_flags":flags})
    return tables
