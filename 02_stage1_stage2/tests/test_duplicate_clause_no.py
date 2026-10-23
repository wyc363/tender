from document_processing.clause_builder import run
from stage2_helpers import block
def test_duplicate_numbers_unique_ids():
    a=block("1.1 甲"); b=block("1.1 乙",page=1); u=run([a,b]); assert len({x.unit_id for x in u})==2
