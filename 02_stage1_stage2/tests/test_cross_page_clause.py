from document_processing.clause_builder import run
from stage2_helpers import block
def test_clause_keeps_page_spans():
    u=run([block("1.1 条款",page=0),block("续文",page=1)])[0]; assert u.pdf_page_end==2 and len(u.source_spans)==2
