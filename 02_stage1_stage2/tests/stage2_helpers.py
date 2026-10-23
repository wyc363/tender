from document_processing.schemas import UnifiedBlock,LogicalUnit,SourceSpan
def block(text="正文",typ="paragraph",page=0,bbox=None):
    return UnifiedBlock(block_id=f"D-p{page}-b0",doc_id="D",page_idx=page,pdf_page=page+1,bbox=bbox or [10,20,100,40],reading_order=0,parser_type=typ,text_raw=text,text_normalized=text,source_file="v2.json",source_block_ids=[f"D-p{page}-b0"])
def unit(text="正文",kind="PARAGRAPH"):
    return LogicalUnit(unit_id="U1",doc_id="D",unit_type=kind,text_raw=text,text_normalized=text,pdf_page_start=1,pdf_page_end=1,source_block_ids=["B1"],source_spans=[SourceSpan(pdf_page=1,page_idx=0,bbox=[0,0,1,1],source_file="x",source_block_id="B1")])
