from document_processing.outline_v2 import resolve_anchors
def test_destination_near_heading_wins_duplicate():
    role={"outline_id":"D-O1","outline_role":"TOPIC_SECTION","raw_title":"第三章 评标办法","destination":{"page_idx":1,"y":100}}
    pages=[[{"native_ref":"toc","text":"第三章 评标办法","bbox_pdf":[0,10,100,20],"font_size_max":10}], [{"native_ref":"body","text":"第三章 评标办法","bbox_pdf":[0,102,100,120],"font_size_max":16}]]
    r=resolve_anchors([role],pages,[{"page_uid":"D-P0"},{"page_uid":"D-P1"}],[])[0]; assert r["anchor"]["native_span_refs"]==["body"]
