from __future__ import annotations
import re
from pathlib import Path
from typing import Protocol
import fitz
from .text_utils_v2 import normalize_text,similarity

SECTION_ROLES={"STRUCTURAL_SECTION","TOPIC_SECTION","ATTACHMENT_SECTION"}
class StructureProvider(Protocol):
    def load(self,document_context: dict)->list[dict]:...

class NativeOutlineProvider:
    def load(self,document_context: dict)->list[dict]:
        doc_id=document_context["doc_id"]; doc=fitz.open(document_context["pdf_path"]); rows=[]
        for i,item in enumerate(doc.get_toc(simple=False),1):
            level,title,page_no,*rest=item; detail=rest[0] if rest and isinstance(rest[0],dict) else {}; page_idx=detail.get("page",page_no-1)
            to=detail.get("to"); x=getattr(to,"x",None); y=getattr(to,"y",None)
            rows.append({"outline_id":f"{doc_id}-O{i:06d}","order":i,"raw_level":level,"raw_title":title,"title_normalized":normalize_text(title),
                         "destination":{"page_uid":f"{doc_id}-P{page_idx:06d}" if isinstance(page_idx,int) else None,"page_idx":page_idx,"x":x,"y":y},
                         "raw_target_page":page_no,"raw_destination":{k:(list(v) if hasattr(v,"__iter__") and not isinstance(v,(str,dict)) else v) for k,v in detail.items() if k!="to"}|({"to":str(to)} if to else {})})
        doc.close(); return rows

def basic_validate(outline,page_registry):
    pages={p["page_idx"]:p for p in page_registry}; previous=None; result=[]
    for o in outline:
        flags=[]; idx=o["destination"]["page_idx"]; x=o["destination"].get("x"); y=o["destination"].get("y")
        if idx not in pages:flags.append("INVALID_DESTINATION")
        elif (x is not None and not 0<=x<=pages[idx]["width_pt"]) or (y is not None and not 0<=y<=pages[idx]["height_pt"]):flags.append("INVALID_DESTINATION_POINT")
        if not o["title_normalized"]:flags.append("EMPTY_TITLE")
        pos=(idx if isinstance(idx,int) else 10**9,y or 0)
        if previous and pos<previous:flags.append("NON_MONOTONIC")
        previous=pos
        result.append({"outline_id":o["outline_id"],"status":"VALID_BASIC" if not flags else "INVALID_BASIC","quality_flags":flags})
    return result

LIST_ITEM=re.compile(r"^[（(]\s*(?:\d+|[一二三四五六七八九十]+)\s*[）)]")
DECIMAL=re.compile(r"^(\d+(?:\.\d+)*\.?)(.*)$")
def normalize_roles(outline,basic):
    valid={x["outline_id"]:x for x in basic}; result=[]
    for o in outline:
        t=o["title_normalized"]; raw=o["raw_title"].strip(); flags=[]; role="UNKNOWN_OUTLINE"
        if valid[o["outline_id"]]["status"]!="VALID_BASIC": role="UNKNOWN_OUTLINE"
        elif re.match(r"^第[一二三四五六七八九十百0-9]+章",t) or o["raw_level"]==1: role="STRUCTURAL_SECTION"
        elif re.match(r"^(附件|附录|附表)",t): role="ATTACHMENT_SECTION"
        elif LIST_ITEM.match(t): role="CONTENT_ANCHOR"
        else:
            m=DECIMAL.match(t)
            punct=raw.endswith(("。","；",";")); sentence=len(t)>42 or punct
            if m:
                number=m.group(1).rstrip("."); depth=number.count(".")+1
                if sentence: role="CONTENT_ANCHOR"
                elif depth>=3: role="CLAUSE_ANCHOR"
                else: role="TOPIC_SECTION"
            elif sentence: role="CONTENT_ANCHOR"
            elif o["raw_level"]<=3 and len(t)<=36: role="TOPIC_SECTION"
            else: role="NONSTRUCTURAL_BOOKMARK"
        boundary="HARD" if role=="STRUCTURAL_SECTION" else "SOFT" if role in {"TOPIC_SECTION","ATTACHMENT_SECTION"} else "NONE"
        result.append({**o,"outline_role":role,"boundary_strength":boundary,"role_confidence":.9 if role!="UNKNOWN_OUTLINE" else .4,"quality_flags":flags})
    return result

def _line_candidates(lines):
    out=[]
    for i,line in enumerate(lines):
        out.append({**line,"native_span_refs":[line["native_ref"]]})
        for width in (2,3):
            group=lines[i:i+width]
            if len(group)==width and all(group[j+1]["bbox_pdf"][1]-group[j]["bbox_pdf"][3]<18 for j in range(width-1)):
                out.append({"text":"".join(x["text"] for x in group),"bbox_pdf":[min(x["bbox_pdf"][0] for x in group),min(x["bbox_pdf"][1] for x in group),max(x["bbox_pdf"][2] for x in group),max(x["bbox_pdf"][3] for x in group)],
                            "font_size_max":max(x["font_size_max"] for x in group),"native_span_refs":[x["native_ref"] for x in group]})
    return out

def resolve_anchors(roles,native_pages,page_registry,atoms):
    atom_pages={}
    for a in atoms:
        if a["representation_role"]=="PRIMARY_SOURCE_ATOM" and a.get("bbox_pdf"):atom_pages.setdefault(a["page_idx"],[]).append(a)
    results=[]
    for o in roles:
        idx=o["destination"]["page_idx"]; dy=o["destination"].get("y"); candidates=[]
        for delta in (0,-1,1):
            p=idx+delta
            if not 0<=p<len(native_pages):continue
            for line in _line_candidates(native_pages[p]):
                sim=similarity(o["raw_title"],line["text"]); dist=abs((line["bbox_pdf"][1])-(dy or line["bbox_pdf"][1])) if delta==0 else 200
                score=.78*sim+.17*max(0,1-dist/180)+.05*min(1,line.get("font_size_max",0)/14)
                candidates.append((score,p,line,"PDF_NATIVE"))
            for a in atom_pages.get(p,[]):
                sim=similarity(o["raw_title"],a["text"]); dist=abs(a["bbox_pdf"][1]-(dy or a["bbox_pdf"][1])) if delta==0 else 200
                score=.76*sim+.19*max(0,1-dist/180)+(.05 if a["parser_type"]=="title" else 0)
                candidates.append((score,p,a,"MINERU_ATOM"))
            if delta==0 and candidates and max(x[0] for x in candidates)>=.78:break
        best=max(candidates,key=lambda x:x[0],default=None); required=o["outline_role"] in SECTION_ROLES
        threshold=.72 if required else .62; status="VERIFIED" if best and best[0]>=threshold else "UNVERIFIED"
        anchor=None
        if best:
            source_obj=best[2]; anchor={"page_uid":page_registry[best[1]]["page_uid"],"page_idx":best[1],"bbox_pdf":source_obj.get("bbox_pdf"),"anchor_source":best[3],
              "source_atom_ids":[source_obj["source_atom_id"]] if best[3]=="MINERU_ATOM" else [],"native_span_refs":source_obj.get("native_span_refs",[])}
        flags=[] if status=="VERIFIED" else ["UNVERIFIED_ANCHOR"]
        results.append({"outline_id":o["outline_id"],"outline_role":o["outline_role"],"status":status,"anchor":anchor,"title_match_score":round(best[0],4) if best else 0,"quality_flags":flags})
    return results

def build_canonical_outline(roles,anchors):
    amap={x["outline_id"]:x for x in anchors}; nodes=[]; stack=[]
    for o in roles:
        a=amap[o["outline_id"]]
        if o["outline_role"] not in SECTION_ROLES or a["status"]!="VERIFIED":continue
        while stack and stack[-1]["raw_level"]>=o["raw_level"]:stack.pop()
        parent=stack[-1]["section_id"] if stack else None; sid=o["outline_id"].replace("-O","-S")
        node={"section_id":sid,"source_outline_id":o["outline_id"],"raw_level":o["raw_level"],"canonical_depth":len(stack)+1,"title_raw":o["raw_title"],"title_normalized":o["title_normalized"],
              "outline_role":o["outline_role"],"boundary_strength":o["boundary_strength"],"parent_id":parent,"children_ids":[],"anchor":a["anchor"],"confidence":a["title_match_score"],"quality_flags":[]}
        if stack:stack[-1]["children_ids"].append(sid)
        nodes.append(node);stack.append(node)
    return nodes
