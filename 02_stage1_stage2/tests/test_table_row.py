from document_processing.table_row_builder import run
from stage2_helpers import block
def test_table_number_is_cell_then_metadata():
    b=block(); t={"table_id":"T","doc_id":"D","rows":[["项号","条款号","名称"],["1","25.1","评标"]],"source":"V2_HTML","source_block_ids":[b.block_id],"quality_flags":[]}; assert run([t],{b.block_id:b})[0].clause_no=="25.1"
