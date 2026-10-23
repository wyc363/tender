from document_processing.clause_builder import run
from stage2_helpers import block
def test_unnumbered_kept(): assert run([block("无编号业务正文")])[0].text_raw
