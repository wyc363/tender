from __future__ import annotations
import copy, html, re
from collections import defaultdict
from .heading_numbering import parse_numbering
from .text_utils_v2 import normalize_text

FORMAL={"STRUCTURAL_SECTION","TOPIC_SECTION","ATTACHMENT_SECTION"}
def pos(obj):
    a=obj.get("anchor") or {}; b=a.get("bbox_pdf") or obj.get("bbox_pdf") or [0,0,0,0]
    return (a.get("page_idx",obj.get("page_idx",0)),float(b[1]))

def normalize_outline_roles_v2(roles):
    out=[]
    for r in roles:
        x=copy.deepcopy(r); t=normalize_text(x["raw_title"]); signals=[]
        applicability=bool(re.match(r"^[（(].*(本项目).*(不包含|不要求|不适用|无须|无需|不采用).*[）)]$",t))
        field=bool(re.search(r"(为：|为:|详见|人民币|[¥￥]|\d+(?:\.\d+)?%|年\s*\d+\s*月)",x["raw_title"]))
        sentence=len(t)>42 or x["raw_title"].strip().endswith(("。","；",";","."))
        mixed_attachment=bool(re.search(r"详见.*附件\s*[一二三四五六123456]",t))
        if applicability: signals.append("APPLICABILITY_STATEMENT_CANDIDATE")
        if field: signals.append("FIELD_VALUE_OR_REFERENCE")
        if sentence: signals.append("COMPLETE_SENTENCE_OR_TERMINAL_PUNCTUATION")
        if mixed_attachment: signals.append("MERGED_CONTENT_AND_ATTACHMENT_TITLE")
        if x["outline_role"] in FORMAL and (applicability or mixed_attachment or (field and sentence)):
            x["outline_role_v1"]=x["outline_role"]; x["outline_role"]="CONTENT_ANCHOR" if not mixed_attachment else "NONSTRUCTURAL_BOOKMARK"
            x["boundary_strength"]="NONE"; x["role_confidence"]=.97; x["role_change_reason_codes"]=signals
        else:
            x["outline_role_v1"]=x["outline_role"]; x["role_change_reason_codes"]=signals
        x["semantic_hint"]="APPLICABILITY_STATEMENT_CANDIDATE" if applicability else None
        out.append(x)
    return out

def _old_parent_map(canonical): return {x["source_outline_id"]:x.get("parent_id") for x in canonical}

def infer_supplementary_sections(doc_id, candidates, roles_v2, anchors, canonical_v1, table_groups, source_rows):
    amap={x["outline_id"]:x for x in anchors}; old_sid_by_oid={x["source_outline_id"]:x["section_id"] for x in canonical_v1}
    native=[]; stack=[]
    for r in roles_v2:
        if r["outline_role"] not in FORMAL or amap[r["outline_id"]]["status"]!="VERIFIED": continue
        while stack and stack[-1]["raw_level"]>=r["raw_level"]: stack.pop()
        native.append({"outline":r,"parent_outline_id":stack[-1]["outline_id"] if stack else None,"position":pos({"anchor":amap[r["outline_id"]]["anchor"]})}); stack.append(r)
    # candidates must sit inside the native body and retain parser/native evidence.
    cand=[]
    for c in candidates:
        if c.get("status")!="PROMOTED": continue
        n=parse_numbering(c["title"])
        cand.append({**copy.deepcopy(c),"numbering":n,"position":pos(c)})
    by_family=defaultdict(list)
    for c in cand:
        if c["numbering"]["family"]!="NONE": by_family[c["numbering"]["family"]].append(c)
    promoted=[]
    # A numbering series with >=2 monotonic members is section evidence.
    series_members=set()
    for fam,items in by_family.items():
        items.sort(key=lambda x:x["position"])
        for i,x in enumerate(items):
            cluster=[y for y in items if abs(y["position"][0]-x["position"][0]) <= 25]
            ords=[y["numbering"]["ordinal"] for y in cluster if y["numbering"]["ordinal"] is not None]
            if len(set(ords))>=2:
                series_members.update(y["local_heading_id"] for y in cluster)
    # native sibling prototype helps SECTION candidates even when only 2/3 are supplementary.
    native_numbered=[]
    for n in native: native_numbered.append((n,parse_numbering(n["outline"]["raw_title"])))
    for c in cand:
        fam=c["numbering"]["family"]; role="LOCAL_HEADING"; evidence=[]; parent_outline=None
        if fam=="ATTACHMENT": role="SUPPLEMENTARY_ATTACHMENT_SECTION"; evidence=["ATTACHMENT_NUMBERING","POSITION_INTERVAL"]
        elif c["local_heading_id"] in series_members and fam in {"CHINESE_PAREN","SECTION"}:
            role="SUPPLEMENTARY_SECTION"; evidence=["NUMBERING_SIBLING","SAME_STYLE","POSITION_INTERVAL"]
        if role=="SUPPLEMENTARY_SECTION" and fam=="SECTION":
            prototypes=[n for n,num in native_numbered if num["family"]==fam and n["position"]<c["position"] and c["position"][0]-n["position"][0]<30]
            if prototypes: parent_outline=prototypes[-1]["parent_outline_id"]; evidence.append("NATIVE_SIBLING_PROTOTYPE")
        if parent_outline is None:
            # infer expected level from the first following native topic; parent is nearest preceding shallower level.
            following=next((n for n in native if n["position"]>c["position"]),None)
            expected=(following["outline"]["raw_level"]-1) if following else 3
            preceding=[n for n in native if n["position"]<c["position"] and n["outline"]["raw_level"]<expected]
            if preceding: parent_outline=preceding[-1]["outline"]["outline_id"]
        if role!="LOCAL_HEADING":
            promoted.append({"supplementary_section_id":f"{doc_id}-V22-X{len(promoted)+1:05d}","title":c["title"],"section_role":role,
                "boundary_strength":"SOFT_SECTION_BOUNDARY","anchor":{"page_uid":c["page_uid"],"page_idx":c["page_idx"],"bbox_pdf":c["bbox_pdf"]},
                "numbering":c["numbering"],"derived_from_local_heading_id":c["local_heading_id"],"source":"SUPPLEMENTARY_PARSER_EVIDENCE",
                "parent_outline_id_hint":parent_outline,"parent_inference":{"selected_parent_id":old_sid_by_oid.get(parent_outline),"evidence":evidence+(["FOLLOWING_NATIVE_TOPIC_PATTERN"] if following else []),"confidence":c["confidence"]},"quality_flags":[]})
    # Derive a missing attachment only when an ordinal gap is bracketed by visible
    # attachment headings and a table occupies the physical interval.
    atoms={x.get("source_atom_id"):x for x in source_rows if x.get("source_atom_id")}
    visible=sorted([x for x in promoted if x["section_role"]=="SUPPLEMENTARY_ATTACHMENT_SECTION"],key=pos)
    for left,right in zip(visible,visible[1:]):
        lo,hi=left["numbering"]["ordinal"],right["numbering"]["ordinal"]
        if lo is None or hi is None or hi-lo<=1 or hi-lo>3 or right["anchor"]["page_idx"]-left["anchor"]["page_idx"]>10: continue
        between=[g for g in table_groups if left["anchor"]["page_idx"]<g["page_start"]<right["anchor"]["page_idx"]]
        for ordinal,g in zip(range(lo+1,hi),between):
            frag=atoms.get(g["physical_fragment_source_atom_ids"][0],{}); num=parse_numbering("附件"+str(ordinal))
            promoted.append({"supplementary_section_id":f"{doc_id}-V22-X{len(promoted)+1:05d}","title":"附件"+str(ordinal),"section_role":"SUPPLEMENTARY_ATTACHMENT_SECTION",
                "boundary_strength":"SOFT_SECTION_BOUNDARY","anchor":{"page_uid":frag.get("page_uid"),"page_idx":frag.get("page_idx"),"bbox_pdf":frag.get("bbox_pdf")},
                "numbering":num,"derived_from_local_heading_id":None,"derived_from_table_group_id":g["table_group_id"],"source":"TABLE_DERIVED_ATTACHMENT",
                "parent_outline_id_hint":left.get("parent_outline_id_hint"),"parent_inference":{"selected_parent_id":left["parent_inference"].get("selected_parent_id"),"evidence":["TABLE_GROUP_TITLE_EVIDENCE","ATTACHMENT_NUMBERING_SEQUENCE","POSITION_INTERVAL"],"confidence":.9},"quality_flags":[]})
    return sorted(promoted,key=pos)

def build_unified_sections(doc_id, roles_v2, anchors, supplementary):
    amap={x["outline_id"]:x for x in anchors}; nodes=[]; stack=[]; oid_to_new={}
    for r in roles_v2:
        if r["outline_role"] not in FORMAL or amap[r["outline_id"]]["status"]!="VERIFIED": continue
        while stack and stack[-1]["raw_level"]>=r["raw_level"]: stack.pop()
        nid=f"{doc_id}-V22-N{len(nodes)+1:05d}"; parent=stack[-1]["section_id"] if stack else None
        n={"section_id":nid,"title":r["raw_title"],"section_role":r["outline_role"],"source":"NATIVE_OUTLINE","derived_from_outline_id":r["outline_id"],
           "raw_level":r["raw_level"],"depth":len(stack)+1,"parent_id":parent,"children_ids":[],"anchor":amap[r["outline_id"]]["anchor"],"quality_flags":[]}
        if stack: stack[-1]["children_ids"].append(nid)
        nodes.append(n); stack.append(n); oid_to_new[r["outline_id"]]=nid
    # Detect a local numbering reset: a same-family ordinal-1 node immediately
    # following a higher-numbered container-like section starts a nested series.
    for prev,cur in zip(nodes,nodes[1:]):
        pn,cn=parse_numbering(prev["title"]),parse_numbering(cur["title"])
        if pn["family"]==cn["family"]=="SECTION" and cn["ordinal"]==1 and pn["ordinal"]!=1 and prev["parent_id"]==cur["parent_id"] and pos(cur)[0]-pos(prev)[0]<=3:
            cur["parent_id"]=prev["section_id"];cur["depth"]=prev["depth"]+1;cur["quality_flags"].append("NUMBERING_RESET_NESTED_SERIES")
    # Insert supplementary nodes and select parent using family sibling prototypes or nearest valid ancestor.
    for s in supplementary:
        parent=oid_to_new.get(s.get("parent_outline_id_hint"))
        fam=s["numbering"]["family"]
        earlier=[x for x in nodes if pos(x)<pos(s) and pos(s)[0]-pos(x)[0]<=20 and parse_numbering(x["title"])["family"]==fam]
        if earlier and fam in {"SECTION","ATTACHMENT"}: parent=earlier[-1]["parent_id"]
        if fam=="ATTACHMENT" and s["numbering"].get("ordinal")==1:
            section_boundaries=[x for x in nodes if pos(x)<pos(s) and x["section_role"] in {"SUPPLEMENTARY_SECTION","STRUCTURAL_SECTION"}]
            if section_boundaries: parent=section_boundaries[-1]["section_id"]
        if not parent and fam=="CHINESE_PAREN" and s["numbering"].get("ordinal")==1:
            section_prototypes=[x for x in nodes if pos(x)<pos(s) and pos(s)[0]-pos(x)[0]<=5 and parse_numbering(x["title"])["family"]=="SECTION"]
            if section_prototypes: parent=section_prototypes[-1]["section_id"]
        if not parent:
            containing=[x for x in nodes if pos(x)<pos(s)]
            parent=max(containing,key=lambda x:(x["depth"],pos(x)),default=None)
            parent=parent["section_id"] if parent else None
        n={"section_id":s["supplementary_section_id"],"title":s["title"],"section_role":s["section_role"],"source":s["source"],"derived_from_local_heading_id":s.get("derived_from_local_heading_id"),
           "derived_from_table_group_id":s.get("derived_from_table_group_id"),"raw_level":None,"depth":by_id_depth(nodes,parent)+1,"parent_id":parent,"children_ids":[],"anchor":s["anchor"],
           "parent_inference":{**s["parent_inference"],"selected_parent_id":parent},"quality_flags":s["quality_flags"]}
        nodes.append(n)
    # Normalize consecutive supplementary siblings after every member exists.
    formal_supp=[x for x in nodes if x["source"]!="NATIVE_OUTLINE" and not x["quality_flags"]]
    formal_supp.sort(key=pos)
    for prev,cur in zip(formal_supp,formal_supp[1:]):
        pn,cn=parse_numbering(prev["title"]),parse_numbering(cur["title"])
        if pn["family"]==cn["family"] and pn["family"] in {"SECTION","ATTACHMENT","CHINESE_PAREN"} and pn["ordinal"] and cn["ordinal"]==pn["ordinal"]+1 and pos(cur)[0]-pos(prev)[0]<=25:
            cur["parent_id"]=prev["parent_id"];cur["depth"]=prev["depth"];cur["parent_inference"]["selected_parent_id"]=prev["parent_id"]
            cur["parent_inference"]["evidence"].append("NUMBERING_SIBLING_PARENT_NORMALIZATION")
    nodes.sort(key=pos); byid={x["section_id"]:x for x in nodes}
    # Reparent native nodes inside each supplementary sibling interval, limited to the selected native ancestor interval.
    supp=[x for x in nodes if x["source"]!="NATIVE_OUTLINE" and not x["quality_flags"]]
    groups=defaultdict(list)
    for x in supp: groups[(x["parent_id"],parse_numbering(x["title"])["family"])].append(x)
    changes=[]
    for (parent,fam),items in groups.items():
        items.sort(key=pos)
        for i,s in enumerate(items):
            end=pos(items[i+1]) if i+1<len(items) else native_interval_end(byid,parent,nodes)
            for n in nodes:
                if n["source"]!="NATIVE_OUTLINE" or not (pos(s)<pos(n)<end): continue
                if n["parent_id"]==parent or is_descendant(byid,n["parent_id"],parent):
                    old=n["parent_id"]; n["parent_id"]=s["section_id"]; n["depth"]=s["depth"]+1
                    changes.append((n["section_id"],old,s["section_id"],"LOCAL_INTERVAL_REPARENTING"))
    # rebuild children after all reparenting
    for n in nodes:n["children_ids"]=[]
    for n in nodes:
        if n["parent_id"] in byid:byid[n["parent_id"]]["children_ids"].append(n["section_id"])
    return nodes,changes

def by_id_depth(nodes,parent):
    x=next((n for n in nodes if n["section_id"]==parent),None); return x["depth"] if x else 0
def is_descendant(byid,node,parent):
    seen=set()
    while node and node not in seen:
        if node==parent:return True
        seen.add(node); node=byid.get(node,{}).get("parent_id")
    return False
def native_interval_end(byid,parent,nodes):
    p=byid.get(parent); start=pos(p) if p else (0,0); depth=p["depth"] if p else 0
    later=[pos(x) for x in nodes if pos(x)>start and x["source"]=="NATIVE_OUTLINE" and x["depth"]<=depth]
    return min(later,default=(10**9,10**9))

def assign_ranges_and_tables(nodes, table_groups, source_rows, page_registry):
    byid={x["section_id"]:x for x in nodes}; atoms={x.get("source_atom_id"):x for x in source_rows if x.get("source_atom_id")}
    for n in nodes:
        start=pos(n); later=[pos(x) for x in nodes if pos(x)>start and x["depth"]<=n["depth"]]
        end=min(later,default=(10**9,10**9)); n["range"]={"start":list(start),"end_exclusive":list(end)}; n["table_group_ids"]=[]; n["content_block_ids"]=[]
    out=[]
    for g0 in table_groups:
        g=copy.deepcopy(g0); first=atoms.get(g["physical_fragment_source_atom_ids"][0],{}); p=(first.get("page_idx",g["page_start"]),(first.get("bbox_pdf") or [0,0])[1])
        owners=[n for n in nodes if tuple(n["range"]["start"])<=p<tuple(n["range"]["end_exclusive"])]
        owner=max(owners,key=lambda x:x["depth"],default=None)
        old=(g.get("canonical_target_ids") or [None])[0]; new=owner["section_id"] if owner else None
        g["old_parent_id"]=old; g["canonical_target_ids"]=[new] if new else []; g["parent_changed"]=old!=new
        if owner:owner["table_group_ids"].append(g["table_group_id"])
        structural_inside=[n for n in nodes if n["section_role"]=="STRUCTURAL_SECTION" and g["page_start"]<=pos(n)[0]<=g["page_end"] and pos(n)>p]
        g["crosses_structural_section_boundary"]=bool(structural_inside); g["quality_flags"]=list(g.get("quality_flags",[]))
        if structural_inside:g["quality_flags"].append("TABLE_GROUP_CROSSES_SECTION_BOUNDARY")
        g["first_fragment_bbox_pdf"]=first.get("bbox_pdf"); g["same_page_latest_section_id"]=new
        g["has_visual_representation"]=bool(g.get("logical_representation_ids"))
        # General signal: raster representation plus very low text-bearing physical fragment and a non-empty logical table image.
        reps={x.get("representation_id"):x for x in source_rows if x.get("representation_id")}; logical=[reps.get(i,{}) for i in g.get("logical_representation_ids",[])]
        physical_text=normalize_text(first.get("text","")); has_image=any(x.get("image_source") for x in logical)
        html_text=normalize_text(re.sub(r"<[^>]+>","",html.unescape(" ".join(x.get("html") or "" for x in logical))))
        page_chars=sum(page_registry[i].get("native_text_char_count",0) for i in range(g["page_start"],g["page_end"]+1)) if page_registry else 0
        coverage=page_chars/max(1,len(html_text))
        requires=bool(has_image and not physical_text and len(html_text)>=40 and coverage<.18)
        g["native_to_logical_text_coverage_ratio"]=round(coverage,4)
        g["requires_image_content_recovery"]=requires; g["image_recovery_reason"]=["NATIVE_TEXT_LOW_COVERAGE","CONTENT_REGION_DOMINATED_BY_RASTER_OR_IMAGE_TABLE"] if requires else []
        out.append(g)
    return out

def build_tree(doc_id,nodes,blocks,local_candidates):
    formal_local={x.get("derived_from_local_heading_id") for x in nodes}
    local=[x for x in local_candidates if x.get("status")=="PROMOTED" and x["local_heading_id"] not in formal_local]
    byid={x["section_id"]:x for x in nodes}
    for b in blocks:
        if not b.get("review_enabled") or b.get("front_matter_role"):continue
        p=(b["page_idx"],(b.get("bbox_pdf") or [0,0])[1]); owners=[n for n in nodes if tuple(n["range"]["start"])<=p<tuple(n["range"]["end_exclusive"])]
        owner=max(owners,key=lambda x:x["depth"],default=None)
        if owner:owner["content_block_ids"].append(b["canonical_block_id"])
    return {"document_id":doc_id,"node_type":"DOCUMENT","body":{"root_section_ids":[x["section_id"] for x in nodes if not x["parent_id"]]},"sections":nodes,"local_headings":local}
