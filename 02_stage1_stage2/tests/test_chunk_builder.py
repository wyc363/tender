from document_processing.chunk_builder import run
from stage2_helpers import unit
def test_hard_split_and_links():
    c=run([unit("甲"*20)],"x.pdf",{"hard_max_chars":10}); assert len(c)==2 and c[0].next_chunk_id==c[1].chunk_id
