from __future__ import annotations
import copy,re
from .heading_numbering import parse_numbering
from .text_utils_v2 import normalize_text

FORMAL_ROLES={"STRUCTURAL_SECTION","TOPIC_SECTION","ATTACHMENT_SECTION","SUPPLEMENTARY_SECTION","SUPPLEMENTARY_ATTACHMENT_SECTION"}
def position(x):
    a=x.get("anchor") or {}; b=a.get("bbox_pdf") or x.get("bbox_pdf") or [0,0,0,0]
    return (a.get("page_idx",x.get("page_idx",0)),float(b[1]))

def build_v21_to_canonical_map(canonical_v21,sections_v22):
    outline_by_old={x["section_id"]:x["source_outline_id"] for x in canonical_v21}
    new_by_outline={x.get("derived_from_outline_id"):x["section_id"] for x in sections_v22 if x.get("derived_from_outline_id")}
    return {old:new_by_outline.get(oid) for old,oid in outline_by_old.items() if new_by_outline.get(oid)}

def migrate_local_heading_parents(local_headings,mapping,canonical_ids):
    out=[];changes=[]
    for h0 in local_headings:
        h=copy.deepcopy(h0);old=h.pop("parent_section_id",None);new=mapping.get(old)
        h["source_parent_section_id_v21"]=old;h["canonical_parent_section_id"]=new
        if new not in canonical_ids:h.setdefault("quality_flags",[]).append("INVALID_CANONICAL_PARENT")
        if old!=new:changes.append({"entity_source_id":h["local_heading_id"],"change_type":"LOCAL_HEADING_PARENT_MIGRATION","old_parent_id":old,"new_parent_id":new,"reason_codes":["V21_TO_V22_DERIVED_FROM_OUTLINE_MAPPING"]})
        out.append(h)
    return out,changes

def _style_compatible(local,a,c):
    lb=local.get("bbox_pdf") or [0,0,0,0];ab=(a.get("anchor") or {}).get("bbox_pdf") or [0,0,0,0];cb=(c.get("anchor") or {}).get("bbox_pdf") or [0,0,0,0]
    heights=[max(1,b[3]-b[1]) for b in (lb,ab,cb)]
    return max(abs(lb[0]-ab[0]),abs(lb[0]-cb[0]))<=24 and max(heights)/min(heights)<=1.45

def _negative_content_signal(text):
    t=normalize_text(text)
    applicability=bool(re.match(r"^[（(].*(本项目).*(不包含|不要求|不适用|无须|无需|不采用).*[）)]$",t))
    field=bool(re.search(r"(为：|为:|详见|人民币|[¥￥]|\d+(?:\.\d+)?%)",text))
    sentence=len(t)>42 or text.strip().endswith(("。","；",";","."))
    return applicability or field or sentence

def promote_numbering_gaps(doc_id,sections,local_headings):
    nodes=copy.deepcopy(sections);byid={x["section_id"]:x for x in nodes};promoted=[];candidate_audit=[]
    for h in local_headings:
        num=parse_numbering(h["title"])
        if num["family"] not in {"CHINESE_DUNHAO","CHINESE_PAREN","SECTION","ATTACHMENT"} or num["ordinal"] is None or h.get("confidence",0)<.75:continue
        same_family=[x for x in nodes if parse_numbering(x["title"])["family"]==num["family"]]
        before=[x for x in same_family if parse_numbering(x["title"])["ordinal"]==num["ordinal"]-1 and position(x)<position(h)]
        after=[x for x in same_family if parse_numbering(x["title"])["ordinal"]==num["ordinal"]+1 and position(x)>position(h)]
        pairs=[(a,c) for a in before for c in after if a.get("parent_id")==c.get("parent_id")]
        if not pairs:continue
        a,c=min(pairs,key=lambda pair:(position(h)[0]-position(pair[0])[0])+(position(pair[1])[0]-position(h)[0]));parent=a.get("parent_id")
        structural_cross=any(x["section_role"]=="STRUCTURAL_SECTION" and position(a)<position(x)<position(c) for x in nodes)
        style=_style_compatible(h,a,c);negative=_negative_content_signal(h["title"])
        passed=style and not negative and not structural_cross
        candidate_audit.append({"local_heading_id":h["local_heading_id"],"title":h["title"],"numbering":num,"parent_id":parent,"preceding_sibling_id":a["section_id"],"following_sibling_id":c["section_id"],"same_style":style,"same_parent_interval":True,"structural_section_crossed":structural_cross,"content_negative_signal":negative,"promoted":passed})
        if not passed:continue
        nid=f"{doc_id}-V221-X{len(promoted)+1:05d}"
        n={"section_id":nid,"title":h["title"],"section_role":"SUPPLEMENTARY_SECTION","source":"SUPPLEMENTARY_NUMBERING_GAP_FILL","derived_from_local_heading_id":h["local_heading_id"],"raw_level":None,"depth":0,"parent_id":parent,"children_ids":[],"anchor":{"page_uid":h["page_uid"],"page_idx":h["page_idx"],"bbox_pdf":h["bbox_pdf"]},"parent_inference":{"selected_parent_id":parent,"evidence":["NUMBERING_GAP_FILL","NUMBERING_SIBLING","SAME_STYLE","POSITION_INTERVAL","SAME_PARENT_INTERVAL"],"confidence":h["confidence"]},"quality_flags":[]}
        nodes.append(n);promoted.append(n)
    promoted_ids={x["derived_from_local_heading_id"] for x in promoted};remaining=[x for x in local_headings if x["local_heading_id"] not in promoted_ids]
    nodes.sort(key=position);rebuild_children(nodes)
    return nodes,remaining,promoted,candidate_audit

def rebuild_children(nodes):
    by={x["section_id"]:x for x in nodes}
    for x in nodes:x["children_ids"]=[]
    for x in nodes:
        if x.get("parent_id") in by:by[x["parent_id"]]["children_ids"].append(x["section_id"])

def recompute_depth(nodes):
    by={x["section_id"]:x for x in nodes};memo={};visiting=set()
    def depth(n):
        sid=n["section_id"]
        if sid in memo:return memo[sid]
        if sid in visiting:raise ValueError("tree cycle")
        visiting.add(sid);p=n.get("parent_id");d=1 if not p else depth(by[p])+1;visiting.remove(sid);memo[sid]=d;return d
    changed=0
    for n in nodes:
        d=depth(n)
        if n.get("depth")!=d:changed+=1
        n["depth"]=d
    inconsistent=[n["section_id"] for n in nodes if n.get("parent_id") and n["depth"]!=by[n["parent_id"]]["depth"]+1]
    rebuild_children(nodes);return changed,inconsistent
