from src.document_processing.tree_consistency_v221 import recompute_depth,promote_numbering_gaps

def test_depth_is_derived_only_from_final_parent_graph():
    nodes=[{"section_id":"A","parent_id":None,"depth":9,"children_ids":[]},{"section_id":"B","parent_id":"A","depth":9,"children_ids":[]},{"section_id":"C","parent_id":"B","depth":1,"children_ids":[]}]
    changed,bad=recompute_depth(nodes)
    assert changed==3 and not bad and [x["depth"] for x in nodes]==[1,2,3]

def test_plain_numbered_list_item_is_not_promoted():
    sections=[]
    locals_=[{"local_heading_id":"L1","title":"二、投标人应当提交材料并承担全部责任。","confidence":.9,"canonical_parent_section_id":"P","page_idx":1,"page_uid":"P1","bbox_pdf":[10,20,100,30]}]
    _,_,promoted,audit=promote_numbering_gaps("X",sections,locals_)
    assert not promoted and not audit

