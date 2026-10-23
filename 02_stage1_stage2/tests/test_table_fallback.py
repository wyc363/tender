from document_processing.table_recovery import run
from stage2_helpers import block
def test_table_fragment_kept():
    b=block("单元格","table"); b.canonical_region="TABLE"; b.owner_id="T"; t=run([b],{})[0]; assert t["source"]=="TABLE_FRAGMENT" and t["rows"]
