from document_processing.structure_reconciler import run
from stage2_helpers import block
def test_text_region():
    b=block(); run([b],{"pages":[{"page_idx":0,"native_table_bboxes":[]}]}); assert b.canonical_region=="TEXT"
