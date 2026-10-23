from src.document_processing.section_intervals_v222 import recompute_section_ranges,interval_diagnostics

def node(s,parent,page,y,depth):return {"section_id":s,"title":s,"section_role":"TOPIC_SECTION","source":"NATIVE_OUTLINE","parent_id":parent,"depth":depth,"anchor":{"page_idx":page,"bbox_pdf":[0,y,10,y+5]},"children_ids":[],"quality_flags":[]}
def test_sibling_ranges_end_at_next_sibling_and_last_inherits_parent_end():
    rows=[node("R1",None,0,10,1),node("A","R1",0,20,2),node("B","R1",0,30,2),node("C","R1",1,10,2),node("R2",None,2,10,1)]
    out,_,_=recompute_section_ranges(rows,4);by={x["section_id"]:x for x in out}
    assert by["A"]["range"]["end_exclusive"]==by["B"]["range"]["start"]
    assert by["B"]["range"]["end_exclusive"]==by["C"]["range"]["start"]
    assert by["C"]["range"]["end_exclusive"]==by["R1"]["range"]["end_exclusive"]
    assert not interval_diagnostics(out)[0]

