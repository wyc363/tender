from document_processing.cleaner import run
from stage2_helpers import block
def test_page_number_disabled():
    b=block("第 2 页","page_number"); run([b],{"page_count":1},{"repeat_page_ratio":.6}); assert not b.review_enabled
