from document_processing.coordinate_normalizer import convert_middle_bbox
def test_per_page_coordinate_transform():
    x=convert_middle_bbox([0,0,1000,1400],[1000,1400],{"width_pt":500,"height_pt":700,"rotation":0}); assert x["bbox_pdf"]==[0,0,500,700] and x["bbox_transform"]["scale_x"]==.5
def test_unresolved_not_guessed(): assert "COORDINATE_TRANSFORM_UNRESOLVED" in convert_middle_bbox([0,0,1,1],None,{"width_pt":1,"height_pt":1,"rotation":0})["quality_flags"]
