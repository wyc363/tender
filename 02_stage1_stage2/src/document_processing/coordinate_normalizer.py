from __future__ import annotations

def convert_middle_bbox(bbox,page_size,page_record):
    if not bbox or not page_size or len(page_size)<2 or not page_size[0] or not page_size[1]:
        return {"bbox_raw":bbox,"bbox_coordinate_space":"MINERU_MIDDLE_PAGE_COORD","bbox_source_page_size":page_size,"bbox_pdf":None,"bbox_pdf_coordinate_space":"PYMUPDF_UNROTATED_PT_TOPLEFT","bbox_norm":None,"bbox_transform":None,"quality_flags":["COORDINATE_TRANSFORM_UNRESOLVED"]}
    sx=page_record["width_pt"]/page_size[0]; sy=page_record["height_pt"]/page_size[1]
    pdf=[bbox[0]*sx,bbox[1]*sy,bbox[2]*sx,bbox[3]*sy]; norm=[bbox[0]/page_size[0],bbox[1]/page_size[1],bbox[2]/page_size[0],bbox[3]/page_size[1]]
    flags=[]; tol=.5
    if not (-tol<=pdf[0]<=pdf[2]<=page_record["width_pt"]+tol and -tol<=pdf[1]<=pdf[3]<=page_record["height_pt"]+tol):flags.append("BBOX_OUT_OF_PAGE")
    return {"bbox_raw":bbox,"bbox_coordinate_space":"MINERU_MIDDLE_PAGE_COORD","bbox_source_page_size":page_size,"bbox_pdf":pdf,
            "bbox_pdf_coordinate_space":"PYMUPDF_UNROTATED_PT_TOPLEFT","bbox_norm":norm,"bbox_transform":{"scale_x":sx,"scale_y":sy,"translate_x":0.0,"translate_y":0.0,"rotation":page_record["rotation"],"source":"middle_page_size"},"quality_flags":flags}

def bbox_iou(a,b):
    if not a or not b:return 0.0
    x0,y0,x1,y1=max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3]); inter=max(0,x1-x0)*max(0,y1-y0)
    aa=max(0,a[2]-a[0])*max(0,a[3]-a[1]); bb=max(0,b[2]-b[0])*max(0,b[3]-b[1]); return inter/(aa+bb-inter) if aa+bb-inter else 0.0
