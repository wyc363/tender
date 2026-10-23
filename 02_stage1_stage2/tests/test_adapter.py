from document_processing.mineru_adapter import _texts
def test_nested_text(): assert _texts({"x":[{"type":"text","content":"甲"}]})==["甲"]
