from document_processing.structure_reconciler import run
from stage2_helpers import block
def test_empty_nonnoise_uncertain():
    b=block(""); run([b],{"pages":[{"page_idx":0,"native_table_bboxes":[]}]}); assert b.owner_type=="UNCERTAIN"

def test_native_only_table_is_uncertain_not_forced():
    b=block(bbox=[0,0,10,10]); run([b],{"pages":[{"page_idx":0,"native_table_bboxes":[[0,0,20,20]]}]}); assert b.owner_type=="UNCERTAIN"
