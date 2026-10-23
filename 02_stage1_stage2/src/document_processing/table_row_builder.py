from __future__ import annotations
import re
from .schemas import LogicalUnit,SourceSpan

def run(tables,block_map):
    units=[]
    for table in tables:
        rows=table["rows"]; header=rows[0] if rows else []; signature=" | ".join(header)
        typ="NOTICE_TABLE" if any("条款" in x or "编列" in x for x in header) else "EVALUATION_TABLE" if any("评分" in x or "评审" in x for x in header) else "GENERIC_TABLE"
        for i,row in enumerate(rows[1:] if len(rows)>1 else rows):
            text=" | ".join(row); clause_no=next((x for x in row[:3] if re.fullmatch(r"\d+(?:\.\d+)+",x)),None)
            bid=table["source_block_ids"][0]; b=block_map[bid]; uid=f"{table['table_id']}-R{i+1:04d}"
            units.append(LogicalUnit(unit_id=uid,doc_id=table["doc_id"],unit_type="TABLE_ROW",text_raw=text,text_normalized=text,
                clause_no=clause_no,clause_title=row[2] if len(row)>2 else None,table_id=table["table_id"],table_row_id=uid,
                pdf_page_start=b.pdf_page,pdf_page_end=b.pdf_page,source_block_ids=table["source_block_ids"],
                source_spans=[SourceSpan(pdf_page=block_map[x].pdf_page,page_idx=block_map[x].page_idx,bbox=block_map[x].bbox,source_file=block_map[x].source_file,source_block_id=x) for x in table["source_block_ids"]],
                parent_unit_id=table["table_id"],structure_confidence=.9 if table["source"]=="V2_HTML" else .5,
                quality_flags=table["quality_flags"]+[typ, "TABLE_HEADER:"+signature[:200]]))
    return units
