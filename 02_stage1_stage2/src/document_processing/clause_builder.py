from __future__ import annotations
import re
from .schemas import LogicalUnit,SourceSpan

START=re.compile(r"^\s*((?:\d+(?:\.\d+)+)|(?:第[一二三四五六七八九十百千0-9]+[条款])|(?:[（(]\d+[）)])|(?:[①②③④⑤⑥⑦⑧⑨⑩])|(?:[一二三四五六七八九十]+、))\s*(.*)$")
REFERENCE=re.compile(r"^(?:具体|详见|参见|按照|依据|符合).{0,12}(?:第)?\d+(?:\.\d+)+(?:条|款)?")
def _span(b): return SourceSpan(pdf_page=b.pdf_page,page_idx=b.page_idx,bbox=b.bbox,source_file=b.source_file,source_block_id=b.block_id)
def run(flow):
    units=[]; current=None; serial=0
    for b in flow:
        m=START.match(b.text_normalized); start=bool(m and not REFERENCE.match(b.text_normalized) and b.parser_type!="title")
        if start:
            serial+=1; uid=f"{b.doc_id}-CLAUSE-{serial:06d}"
            current=LogicalUnit(unit_id=uid,doc_id=b.doc_id,unit_type="CLAUSE",text_raw=b.text_raw,text_normalized=b.text_normalized,
                clause_no=m.group(1),clause_title=(m.group(2)[:120] or None),pdf_page_start=b.pdf_page,pdf_page_end=b.pdf_page,
                source_block_ids=[b.block_id],source_spans=[_span(b)],structure_confidence=.82,quality_flags=[])
            units.append(current)
        elif current and b.parser_type!="title" and len(current.text_normalized)+len(b.text_normalized)<=1800:
            current.text_raw += "\n"+b.text_raw; current.text_normalized += " "+b.text_normalized; current.pdf_page_end=b.pdf_page
            current.source_block_ids.append(b.block_id); current.source_spans.append(_span(b))
        else:
            serial+=1; uid=f"{b.doc_id}-PARA-{serial:06d}"; current=None
            units.append(LogicalUnit(unit_id=uid,doc_id=b.doc_id,unit_type="PARAGRAPH",text_raw=b.text_raw,text_normalized=b.text_normalized,
                clause_title=b.text_normalized[:120] if b.parser_type=="title" else None,pdf_page_start=b.pdf_page,pdf_page_end=b.pdf_page,
                source_block_ids=[b.block_id],source_spans=[_span(b)],structure_confidence=.72,quality_flags=["TITLE_SOURCE"] if b.parser_type=="title" else []))
    return units
