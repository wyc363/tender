from document_processing.structure_reconciler import run
from stage2_helpers import block
def test_native_table_owns_text():
    b=block(bbox=[0,0,10,10]); t=block("<table>","table",bbox=[0,10,10,20]); t.block_id="T"; t.source_block_ids=["T"]
    run([b,t],{"pages":[{"page_idx":0,"native_table_bboxes":[[0,0,20,20]]}]}); assert b.owner_type=="TABLE" and not b.text_flow_enabled
