from document_processing.cross_page_merger import run
from stage2_helpers import unit
def test_uncertain_tables_stay_separate(): assert len(run([unit("a","TABLE_ROW"),unit("b","TABLE_ROW")]))==2
