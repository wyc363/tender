from document_processing.clause_builder import run
from stage2_helpers import block
def test_reference_not_clause(): assert run([block("具体按照第3.1.9条执行")])[0].unit_type=="PARAGRAPH"
