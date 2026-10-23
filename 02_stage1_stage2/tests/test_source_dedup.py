from document_processing.mineru_adapter import deduplicate_source_blocks
from stage2_helpers import block
def test_source_dedup():
    a=block(); b=a.model_copy(deep=True); b.source_file="fallback.json"; assert len(deduplicate_source_blocks([a,b]))==1
