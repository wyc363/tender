from document_processing.parse_planner import local_to_original_page


def test_page_offset():
    original_page_idx, physical_pdf_page = local_to_original_page(0, 83)
    assert original_page_idx == 83
    assert physical_pdf_page == 84

