from document_processing.outline_v2 import build_canonical_outline
def test_stack_parent_relationship():
    levels=[1,2,3,4,4,3]; roles=[{"outline_id":f"D-O{i}","raw_level":l,"raw_title":str(i),"title_normalized":str(i),"outline_role":"TOPIC_SECTION","boundary_strength":"SOFT"} for i,l in enumerate(levels)]
    anchors=[{"outline_id":x["outline_id"],"status":"VERIFIED","anchor":{},"title_match_score":1} for x in roles]; n=build_canonical_outline(roles,anchors); assert n[4]["parent_id"]==n[2]["section_id"] and n[5]["parent_id"]==n[1]["section_id"]
