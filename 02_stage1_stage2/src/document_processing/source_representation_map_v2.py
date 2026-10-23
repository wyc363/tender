from __future__ import annotations
import json
from pathlib import Path
from .coordinate_normalizer import convert_middle_bbox
from .text_utils_v2 import extract_text,middle_text,normalize_text

def load(doc_id: str,raw_dir: Path,page_registry: list[dict]):
    v2_path=next(raw_dir.glob("*_content_list_v2.json")); middle_path=next(raw_dir.glob("*_middle.json")); fallback_path=next(raw_dir.glob("*_content_list.json"))
    v2=json.loads(v2_path.read_text(encoding="utf-8")); middle=json.loads(middle_path.read_text(encoding="utf-8")); fallback=json.loads(fallback_path.read_text(encoding="utf-8"))
    atoms=[]; reps=[]; v2_records=[]; middle_lookup={}; middle_lookup_any={}; middle_by_type={}; used_middle=set()
    for page_idx,p in enumerate(middle["pdf_info"]):
        size=p.get("page_size")
        for source_list,kind in ((p.get("preproc_blocks",[]),"PREPROC"),(p.get("discarded_blocks",[]),"DISCARDED")):
            for j,b in enumerate(source_list):
                text=middle_text(b); key=(page_idx,normalize_text(text),b.get("type")); middle_lookup.setdefault(key,[]).append(b); middle_lookup_any.setdefault((page_idx,normalize_text(text)),[]).append(b); middle_by_type.setdefault((page_idx,b.get("type")),[]).append(b)
                if b.get("type") in {"table","image"} or kind=="DISCARDED":
                    aid=f"{doc_id}-MID-P{page_idx:06d}-{kind[:3]}-{j:04d}"; coord=convert_middle_bbox(b.get("bbox"),size,page_registry[page_idx])
                    role="DISCARDED_SOURCE_ATOM" if kind=="DISCARDED" else "PHYSICAL_PRIMARY_SOURCE_ATOM"
                    atoms.append({"source_atom_id":aid,"representation_role":role,"source_file":str(middle_path),"page_uid":page_registry[page_idx]["page_uid"],"page_idx":page_idx,
                                  "parser_type":b.get("type"),"text":text,"source_payload_ref":{"collection":kind.lower(),"index":j},**coord,"canonical_target_ids":[]})
    for page_idx,page in enumerate(v2):
        for j,item in enumerate(page):
            typ=item.get("type","unknown"); rid=f"{doc_id}-V2-P{page_idx:06d}-R{j:04d}"; text=extract_text(item.get("content",{})); html=item.get("content",{}).get("html") if isinstance(item.get("content"),dict) else None
            if typ=="table":
                reps.append({"representation_id":rid,"representation_role":"LOGICAL_REPRESENTATION","source_file":str(v2_path),"page_idx_hint":page_idx,"parser_type":typ,"text":text,"html":html,"image_source":item.get("content",{}).get("image_source"),"canonical_target_ids":[],"physical_source_atom_ids":[]})
                continue
            if typ in {"page_header","page_footer","page_number"}:
                reps.append({"representation_id":rid,"representation_role":"LOGICAL_REPRESENTATION","source_file":str(v2_path),"page_idx_hint":page_idx,"parser_type":typ,"text":text,"canonical_target_ids":[]})
                continue
            middle_type="text" if typ=="paragraph" else typ
            key=(page_idx,normalize_text(text),middle_type); candidates=middle_lookup.get(key,[])
            if not candidates: candidates=middle_lookup_any.get((page_idx,normalize_text(text)),[])
            matched=next((b for b in candidates if id(b) not in used_middle),None)
            if not matched: matched=next((b for b in middle_by_type.get((page_idx,middle_type),[]) if id(b) not in used_middle),None)
            if not matched and typ in {"paragraph","list"}:
                matched=next((b for mt in ("text","list","ref_text") for b in middle_by_type.get((page_idx,mt),[]) if id(b) not in used_middle),None)
            if matched: used_middle.add(id(matched))
            if matched:
                size=middle["pdf_info"][page_idx].get("page_size"); coord=convert_middle_bbox(matched.get("bbox"),size,page_registry[page_idx]); coord["bbox_raw_v2"]=item.get("bbox"); coord["bbox_coordinate_space_v2"]="MINERU_V2_RENDER_COORD_UNCALIBRATED"
            else:
                coord={"bbox_raw":item.get("bbox"),"bbox_coordinate_space":"MINERU_V2_RENDER_COORD_UNCALIBRATED","bbox_source_page_size":None,"bbox_pdf":None,"bbox_pdf_coordinate_space":"PYMUPDF_UNROTATED_PT_TOPLEFT","bbox_norm":None,"bbox_transform":None,"quality_flags":["COORDINATE_TRANSFORM_UNRESOLVED"]}
            aid=f"{doc_id}-V2-P{page_idx:06d}-A{j:04d}"; atom={"source_atom_id":aid,"representation_id":rid,"representation_role":"PRIMARY_SOURCE_ATOM","source_file":str(v2_path),"page_uid":page_registry[page_idx]["page_uid"],"page_idx":page_idx,"parser_type":typ,"text":text,"html":html,"source_payload_ref":{"page_index":page_idx,"item_index":j},**coord,"canonical_target_ids":[]}
            atoms.append(atom); v2_records.append((item,atom))
    for j,item in enumerate(fallback): reps.append({"representation_id":f"{doc_id}-CL-R{j:06d}","representation_role":"FALLBACK_REPRESENTATION","fallback_activated":False,"source_file":str(fallback_path),"page_idx_hint":item.get("page_idx"),"parser_type":item.get("type"),"text":item.get("text","")})
    return atoms,reps,v2_records,middle
