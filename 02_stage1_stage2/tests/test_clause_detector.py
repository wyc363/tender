from document_processing.clause_builder import run
from stage2_helpers import block
def test_clause_start(): assert run([block("3.1.9 投标人的类似工程业绩要求")])[0].unit_type=="CLAUSE"
