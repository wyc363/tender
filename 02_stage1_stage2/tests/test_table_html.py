from document_processing.table_recovery import run
from stage2_helpers import block
def test_html_rowspan_text():
    b=block("","table"); b.html="<table><tr><th>A</th></tr><tr><td rowspan='2'>B</td></tr></table>"; b.canonical_region="TABLE"; b.owner_id="T"; assert run([b],{})[0]["rows"]==[["A"],["B"]]
