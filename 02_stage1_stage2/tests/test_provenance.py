from document_processing.chunk_builder import run
from stage2_helpers import unit
def test_chunk_provenance():
    c=run([unit()],"x.pdf",{"hard_max_chars":100})[0]; assert c.source_spans[0].source_block_id in c.source_block_ids
