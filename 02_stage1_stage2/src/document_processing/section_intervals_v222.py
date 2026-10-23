from __future__ import annotations
import copy

FORMAL_ROLES={"STRUCTURAL_SECTION","TOPIC_SECTION","ATTACHMENT_SECTION","SUPPLEMENTARY_SECTION","SUPPLEMENTARY_ATTACHMENT_SECTION"}
def base_position(obj):
    anchor=obj.get("anchor") or {};bbox=anchor.get("bbox_pdf") or obj.get("bbox_pdf") or [0,0,0,0]
    return (int(anchor.get("page_idx",obj.get("page_idx",0))),float(bbox[1]))
def position3(obj,event_order=0):
    p=base_position(obj);return (p[0],p[1],event_order)

def build_section_event_stream(sections):
    formal=[x for x in sections if x.get("section_role") in FORMAL_ROLES]
    ordered=sorted(formal,key=lambda x:(base_position(x),x.get("depth",1),x["section_id"]))
    per_page={}
    events=[]
    for x in ordered:
        page,y=base_position(x);key=(page,y);order=per_page.get(key,0);per_page[key]=order+1
        x["canonical_position"]=[page,y,order]
        events.append({"event_type":"SECTION_ANCHOR","section_id":x["section_id"],"canonical_position":[page,y,order],"parent_id":x.get("parent_id"),"depth":x["depth"]})
    return events

def recompute_section_ranges(sections,document_page_count):
    nodes=copy.deepcopy(sections);by={x["section_id"]:x for x in nodes};events=build_section_event_stream(nodes)
    children={None:[]}
    for n in nodes:children.setdefault(n.get("parent_id"),[]).append(n)
    for rows in children.values():rows.sort(key=lambda x:tuple(x["canonical_position"]))
    old_ranges={x["section_id"]:copy.deepcopy(x.get("range")) for x in nodes};audits=[]
    def assign_siblings(parent_id,parent_end):
        rows=children.get(parent_id,[])
        for i,n in enumerate(rows):
            end=tuple(rows[i+1]["canonical_position"]) if i+1<len(rows) else parent_end
            n["range"]={"start":list(n["canonical_position"]),"end_exclusive":list(end)}
            audits.append({"section_id":n["section_id"],"old_range":old_ranges[n["section_id"]],"new_range":n["range"],"range_changed":old_ranges[n["section_id"]]!=n["range"],"reason":"FINAL_SIBLING_INTERVAL_RECOMPUTE"})
            assign_siblings(n["section_id"],end)
    assign_siblings(None,(document_page_count,0.0,0))
    return nodes,events,audits

def contains(section,point):
    return tuple(section["range"]["start"])<=tuple(point)<tuple(section["range"]["end_exclusive"])

def reassign_content_ownership(sections,blocks,old_sections):
    nodes=sections;old_owner={bid:n["section_id"] for n in old_sections for bid in n.get("content_block_ids",[])}
    for n in nodes:n["content_block_ids"]=[]
    audits=[];multiple=0;unowned=[]
    for order,b in enumerate(sorted(blocks,key=lambda x:(x["page_idx"],(x.get("bbox_pdf") or [0,0])[1],x["canonical_block_id"]))):
        if not b.get("review_enabled") or b.get("front_matter_role"):continue
        point=[b["page_idx"],float((b.get("bbox_pdf") or [0,0])[1]),1]
        eligible=[n for n in nodes if contains(n,point)]
        if not eligible:unowned.append(b["canonical_block_id"]);continue
        max_depth=max(x["depth"] for x in eligible);deep=[x for x in eligible if x["depth"]==max_depth]
        if len(deep)>1:multiple+=1
        owner=sorted(deep,key=lambda x:tuple(x["canonical_position"]),reverse=True)[0];owner["content_block_ids"].append(b["canonical_block_id"])
        old=old_owner.get(b["canonical_block_id"]);audits.append({"canonical_block_id":b["canonical_block_id"],"canonical_position":point,"old_owner_section_id":old,"new_owner_section_id":owner["section_id"],"owner_changed":old!=owner["section_id"],"reason":"DEEPEST_FINAL_SECTION_INTERVAL"})
    mismatches=[a for a in audits if not contains(next(x for x in nodes if x["section_id"]==a["new_owner_section_id"]),a["canonical_position"])]
    return audits,multiple,mismatches,unowned

def reverify_table_ownership(sections,table_groups):
    for n in sections:n["table_group_ids"]=[]
    audits=[];mismatches=[];conflicts=[]
    for g0 in table_groups:
        g=copy.deepcopy(g0);bbox=g.get("first_fragment_bbox_pdf") or [0,0,0,0];point=[g["page_start"],float(bbox[1]),2]
        eligible=[n for n in sections if contains(n,point)]
        if not eligible:
            mismatches.append(g["table_group_id"]);g["quality_flags"]=list(g.get("quality_flags",[]))+["TABLE_GROUP_PARENT_INTERVAL_MISMATCH"];audits.append({"table_group_id":g["table_group_id"],"old_parent_id":(g.get("canonical_target_ids") or [None])[0],"new_parent_id":None,"canonical_position":point,"parent_changed":True});conflicts.append(g);continue
        owner=max(eligible,key=lambda x:x["depth"]);old=(g.get("canonical_target_ids") or [None])[0];g["canonical_target_ids"]=[owner["section_id"]];owner["table_group_ids"].append(g["table_group_id"])
        audits.append({"table_group_id":g["table_group_id"],"old_parent_id":old,"new_parent_id":owner["section_id"],"canonical_position":point,"parent_changed":old!=owner["section_id"],"new_parent_interval":owner["range"]})
        if not contains(owner,point):mismatches.append(g["table_group_id"])
        conflicts.append(g)
    return conflicts,audits,mismatches

def interval_diagnostics(sections):
    by_parent={}
    for n in sections:by_parent.setdefault(n.get("parent_id"),[]).append(n)
    intermediate=[]
    for rows in by_parent.values():
        rows.sort(key=lambda x:tuple(x["canonical_position"]))
        for a,b in zip(rows,rows[1:]):
            if tuple(a["range"]["end_exclusive"])!=tuple(b["range"]["start"]):intermediate.append({"section_id":a["section_id"],"intermediate_section_id":b["section_id"]})
    promoted=[x for x in sections if x.get("source")!="NATIVE_OUTLINE"]
    missing_range=[x["section_id"] for x in promoted if not x.get("range")]
    required=("content_block_ids","table_group_ids")
    missing_fields=[x["section_id"] for x in promoted if any(k not in x for k in required)]
    return intermediate,missing_range,missing_fields
