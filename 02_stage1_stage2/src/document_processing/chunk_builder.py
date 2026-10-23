from __future__ import annotations
from .schemas import Chunk
def run(units,file_name,config):
    chunks=[]; hard=config["hard_max_chars"]
    for unit in units:
        text=unit.text_raw
        pieces=[text[i:i+hard] for i in range(0,len(text),hard)] or [""]
        for j,piece in enumerate(pieces):
            cid=f"{unit.doc_id}-C{len(chunks)+1:07d}"; flags=list(unit.quality_flags)
            if len(pieces)>1: flags.append("HARD_WINDOW_SPLIT")
            chunks.append(Chunk(chunk_id=cid,doc_id=unit.doc_id,file_name=file_name,chunk_type=unit.unit_type,text_raw=piece,
                text_normalized=" ".join(piece.split()),text_for_retrieval=" | ".join(x for x in [unit.clause_no or "",unit.clause_title or "",piece] if x),
                clause_no=unit.clause_no,clause_title=unit.clause_title,table_id=unit.table_id,table_row_id=unit.table_row_id,
                section_path=unit.section_path,pdf_page_start=unit.pdf_page_start,pdf_page_end=unit.pdf_page_end,
                source_spans=unit.source_spans,source_block_ids=unit.source_block_ids,parent_unit_id=unit.unit_id,
                structure_confidence=unit.structure_confidence,quality_flags=flags))
    for i,c in enumerate(chunks):
        c.prev_chunk_id=chunks[i-1].chunk_id if i else None; c.next_chunk_id=chunks[i+1].chunk_id if i+1<len(chunks) else None
    return chunks
