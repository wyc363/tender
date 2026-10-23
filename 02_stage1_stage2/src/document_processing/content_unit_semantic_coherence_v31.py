from __future__ import annotations

import copy,re
from collections import Counter
from pathlib import Path
from typing import Any

from .content_units_v3 import normalize_text,parse_clause_start,parse_list_items,parse_table_html,_middle_table_html

LEAD_IN=re.compile(r"^\s*(?:注|说明|备注|填写说明|填表说明|要求)\s*[:：]?\s*$")
HEADING_LINE=re.compile(r"^\s*(?:(?:[（(][一二三四五六七八九十]+[）)])|(?:[一二三四五六七八九十]+、)|(?:附件\s*[一二三四五六七八九十0-9]+)|(?:附表\s*\d+)|(?:\d+\s*[-－—]\s*\d+))[\s.、:：]*(\S.*)?$")
LIST_LINE=re.compile(r"^\s*(?:[（(]\d+[）)]|\d+[）)]|[①②③④⑤⑥⑦⑧⑨⑩])")
FORM_TITLE=re.compile(r"(?:证明书|申请函|承诺函|授权书|简历表|报价表|附表)\s*$")
SENTENCE_END=re.compile(r"[。！？；：.!?;）】》”’]$")

def is_bare_instruction(unit:dict)->bool:
    return unit.get("unit_type")=="FORM_INSTRUCTION_UNIT" and bool(LEAD_IN.match(unit.get("normalized_text") or unit.get("text", "")))

def _merge_provenance(items:list[dict])->list[dict]:
    seen=set(); out=[]
    for x in items:
        key=(x.get("page_idx"),tuple(x.get("bbox_pdf") or []),x.get("canonical_block_id"),x.get("source_atom_id"))
        if key not in seen: seen.add(key);out.append(x)
    return out

def compose_form_instructions(units:list[dict]):
    out=[];audit=[];changes=[];i=0
    absorbable={"LIST_GROUP_UNIT","PARAGRAPH_UNIT","CLAUSE_UNIT","FORM_INSTRUCTION_UNIT"}
    while i<len(units):
        current=copy.deepcopy(units[i])
        if is_bare_instruction(current) and i+1<len(units):
            first=units[i+1]; family=first["unit_type"]; absorbed=[];j=i+1
            allowed={"LIST_GROUP_UNIT":{"LIST_GROUP_UNIT","PARAGRAPH_UNIT"},"PARAGRAPH_UNIT":{"PARAGRAPH_UNIT"},"CLAUSE_UNIT":{"CLAUSE_UNIT","PARAGRAPH_UNIT"},"FORM_INSTRUCTION_UNIT":{"FORM_INSTRUCTION_UNIT"}}.get(family,set())
            while j<len(units):
                nxt=units[j];same=current["section_id"]==nxt["section_id"] and nxt["unit_type"] in allowed and nxt["page_start"]-current["page_end"]<=1
                no_boundary=(not nxt.get("local_heading_context") or nxt.get("local_heading_context")==current.get("local_heading_context")) and not is_bare_instruction(nxt)
                if not (same and no_boundary):break
                absorbed.append(nxt);current["text"] += "\n"+nxt["text"];current["page_end"]=nxt["page_end"]
                current["primary_source_block_ids"]+=nxt["primary_source_block_ids"];current["context_source_block_ids"]+=nxt["context_source_block_ids"]
                current["source_atom_ids"]+=nxt["source_atom_ids"];current["native_span_refs"]+=nxt["native_span_refs"];current["provenance"]+=nxt["provenance"];current["list_items"]+=nxt.get("list_items",[]);j+=1
            if absorbed:
                current["normalized_text"]=normalize_text(current["text"]);current["source_atom_ids"]=list(dict.fromkeys(current["source_atom_ids"]));current["native_span_refs"]=list(dict.fromkeys(current["native_span_refs"]));current["provenance"]=_merge_provenance(current["provenance"]);current["cross_page"]=current["page_start"]!=current["page_end"]
                old_ids=[units[i]["unit_id"]]+[x["unit_id"] for x in absorbed];current["composed_from_unit_ids"]=old_ids
                audit.append({"lead_unit_id":units[i]["unit_id"],"absorbed_unit_ids":old_ids[1:],"result_unit_id":current["unit_id"],"page_start":current["page_start"],"page_end":current["page_end"]})
                changes.append({"old_unit_ids":old_ids,"new_unit_ids":[current["unit_id"]],"change_type":"MERGED_FORM_INSTRUCTION"});i=j;out.append(current);continue
        out.append(current);i+=1
    return out,audit,changes

def _heading_offsets(text:str)->list[tuple[int,int,str]]:
    lines=list(re.finditer(r"(?m)^.*(?:\n|$)",text or "")); out=[]
    for m in lines:
        line=m.group(0).strip()
        if not line or LIST_LINE.match(line): continue
        hit=HEADING_LINE.match(line)
        if hit and len(line)<=100 and (hit.group(1) or FORM_TITLE.search(line)): out.append((m.start(),m.end(),line))
    return out

def split_internal_boundaries(units:list[dict],doc_id:str):
    out=[];audit=[];changes=[];serial=0
    eligible={"PARAGRAPH_UNIT","FORM_BODY_UNIT","FORM_INSTRUCTION_UNIT","LIST_GROUP_UNIT"}
    for original in units:
        offsets=_heading_offsets(original.get("text", "")) if original["unit_type"] in eligible else []
        if len(offsets)<2: out.append(copy.deepcopy(original));continue
        starts=[0]+[x[0] for x in offsets if x[0]>0];starts=sorted(set(starts));pieces=[]
        for idx,start in enumerate(starts):
            end=starts[idx+1] if idx+1<len(starts) else len(original["text"])
            if original["text"][start:end].strip(): pieces.append((start,end,original["text"][start:end].strip()))
        new_ids=[]
        for idx,(start,end,text) in enumerate(pieces):
            unit=copy.deepcopy(original)
            if idx:
                serial+=1;unit["unit_id"]=f"{doc_id}-V31S{serial:05d}";unit["primary_source_block_ids"]=[];unit["context_source_block_ids"]=list(dict.fromkeys(original["context_source_block_ids"]+original["primary_source_block_ids"]))
            unit["text"]=text;unit["normalized_text"]=normalize_text(text);unit["source_text_span"]=[start,end]
            unit["list_items"]=parse_list_items(text);new_ids.append(unit["unit_id"]);out.append(unit)
        audit.append({"old_unit_id":original["unit_id"],"new_unit_ids":new_ids,"boundaries":[{"start":x[0],"end":x[1],"text":x[2]} for x in offsets]})
        changes.append({"old_unit_ids":[original["unit_id"]],"new_unit_ids":new_ids,"change_type":"SPLIT_INTERNAL_HEADING"})
    return out,audit,changes

def _looks_structural_start(unit:dict)->bool:
    text=unit.get("normalized_text","")
    return bool(parse_clause_start(text) or LIST_LINE.match(text) or HEADING_LINE.match(text) or FORM_TITLE.search(text)) or unit["unit_type"] in {"FORM_BODY_UNIT","TABLE_ROW_GROUP_UNIT"}

def merge_cross_page_text(units:list[dict]):
    out=[];audit=[];changes=[];i=0
    while i<len(units):
        cur=copy.deepcopy(units[i])
        if i+1<len(units):
            nxt=units[i+1]; tail=cur.get("normalized_text","");head=nxt.get("normalized_text","")
            candidate=(cur["unit_type"] in {"PARAGRAPH_UNIT","LIST_GROUP_UNIT","CLAUSE_UNIT"} and nxt["unit_type"]=="PARAGRAPH_UNIT" and cur["section_id"]==nxt["section_id"] and cur["page_end"]+1==nxt["page_start"] and len(head)<=40 and not SENTENCE_END.search(tail) and not _looks_structural_start(nxt) and cur.get("local_heading_context")==nxt.get("local_heading_context"))
            if candidate:
                separator="" if cur["unit_type"]=="LIST_GROUP_UNIT" else ""
                cur["text"]+=separator+nxt["text"];cur["normalized_text"]=normalize_text(cur["text"]);cur["page_end"]=nxt["page_end"]
                cur["primary_source_block_ids"]+=nxt["primary_source_block_ids"];cur["context_source_block_ids"]+=nxt["context_source_block_ids"]
                cur["source_atom_ids"]=list(dict.fromkeys(cur["source_atom_ids"]+nxt["source_atom_ids"]));cur["native_span_refs"]=list(dict.fromkeys(cur["native_span_refs"]+nxt["native_span_refs"]));cur["provenance"]=_merge_provenance(cur["provenance"]+nxt["provenance"]);cur["cross_page"]=True
                if cur["unit_type"]=="LIST_GROUP_UNIT": cur["list_items"]=parse_list_items(cur["text"])
                audit.append({"previous_unit_id":units[i]["unit_id"],"continuation_unit_id":nxt["unit_id"],"result_unit_id":cur["unit_id"],"page_start":cur["page_start"],"page_end":cur["page_end"]})
                changes.append({"old_unit_ids":[units[i]["unit_id"],nxt["unit_id"]],"new_unit_ids":[cur["unit_id"]],"change_type":"MERGED_CROSS_PAGE_CONTINUATION"});out.append(cur);i+=2;continue
        out.append(cur);i+=1
    return out,audit,changes

def recovery_priority(unit:dict)->str:
    table=unit.get("table_context") or {}
    context=" ".join(unit.get("section_path",[])+[table.get("table_title","")]+table.get("column_headers",[]))
    if re.search(r"评分|评标|评审|资格|投标须知|定标",context): return "HIGH"
    if re.search(r"合同|技术|附件|格式",context): return "MEDIUM"
    return "LOW"

def selective_table_recovery(units:list[dict],tables:list[dict],source_map:list[dict]):
    out=copy.deepcopy(units);audit=[];changes=[];reps={x.get("representation_id"):x for x in source_map if x.get("representation_id")};atoms={x.get("source_atom_id"):x for x in source_map if x.get("source_atom_id")};table_by={x["table_group_id"]:x for x in tables};cache={}
    for unit in out:
        if unit.get("reconstruction_status")!="RECOVERY_REQUIRED" or not unit.get("source_table_group_ids"): continue
        tid=unit["source_table_group_ids"][0];table=table_by.get(tid,{});priority=recovery_priority(unit);method=None;confidence=0.0;evidence=[];recovered=""
        target_pages={p.get("page_idx") for p in unit.get("provenance",[]) if p.get("page_idx") is not None} or set(range(unit["page_start"],unit["page_end"]+1))
        for rid in table.get("logical_representation_ids",[]):
            rep=reps.get(rid,{})
            if rep.get("page_idx_hint") not in target_pages: continue
            rows=parse_table_html(rep.get("html") or "")
            if rows:
                recovered="表格结构："+" | ".join(rows[0]);method="V2_LOGICAL";confidence=.95;evidence=[{"representation_id":rid,"page_idx":rep.get("page_idx_hint"),"image_source":rep.get("image_source")}];break
        if not recovered:
            for atom_id in unit.get("source_atom_ids",[]):
                atom=atoms.get(atom_id,{}) ;html=_middle_table_html(atom,cache);rows=parse_table_html(html)
                if rows:
                    recovered="表格结构："+" | ".join(rows[0]);method="MIDDLE_PHYSICAL";confidence=.9;evidence=[{"source_atom_id":atom_id,"page_idx":atom.get("page_idx"),"bbox_pdf":atom.get("bbox_pdf")}];break
                row_count=len(re.findall(r"<tr\b",html,re.I));cell_count=len(re.findall(r"<t[dh]\b",html,re.I))
                if row_count and cell_count:
                    recovered=f"空白表格模板：{row_count}行，{cell_count//row_count if row_count else 0}列";method="MIDDLE_PHYSICAL";confidence=.99;evidence=[{"source_atom_id":atom_id,"page_idx":atom.get("page_idx"),"bbox_pdf":atom.get("bbox_pdf"),"empty_grid":True}];break
        success=bool(recovered)
        unit["recovery_priority"]=priority;unit["recovery_attempted"]=True;unit["recovery_method"]=method;unit["recovery_confidence"]=confidence;unit["recovery_evidence"]=evidence
        if success:
            unit["text"]=recovered;unit["normalized_text"]=normalize_text(recovered);unit["reconstruction_status"]="OK";unit["quality_flags"]=[x for x in unit["quality_flags"] if "RECOVERY_REQUIRED" not in x]
            unit["recovered_from_original_evidence"]=True;changes.append({"old_unit_ids":[unit["unit_id"]],"new_unit_ids":[unit["unit_id"]],"change_type":"TABLE_RECOVERY_UPDATED"})
        else: unit["recovery_failure_reason"]="NO_RELIABLE_BBOX_SCOPED_TEXT_OR_TABLE_STRUCTURE"
        audit.append({"unit_id":unit["unit_id"],"table_group_id":tid,"priority":priority,"attempted":True,"success":success,"recovery_method":method,"confidence":confidence,"evidence":evidence,"failure_reason":unit.get("recovery_failure_reason")})
    return out,audit,changes

def semantic_metrics(units:list[dict],tables:list[dict]):
    bare=sum(is_bare_instruction(x) for x in units)
    internal=sum(len(_heading_offsets(x.get("text","")))>=2 for x in units if x["unit_type"] in {"PARAGRAPH_UNIT","FORM_BODY_UNIT","FORM_INSTRUCTION_UNIT","LIST_GROUP_UNIT"})
    orphan=0
    for a,b in zip(units,units[1:]):
        if a["unit_type"] in {"PARAGRAPH_UNIT","LIST_GROUP_UNIT","CLAUSE_UNIT"} and b["unit_type"]=="PARAGRAPH_UNIT" and a["section_id"]==b["section_id"] and a["page_end"]+1==b["page_start"] and len(b.get("normalized_text",""))<=40 and not SENTENCE_END.search(a.get("normalized_text","")) and not _looks_structural_start(b) and a.get("local_heading_context")==b.get("local_heading_context"): orphan+=1
    recovery=[x for x in units if x.get("reconstruction_status")=="RECOVERY_REQUIRED"]
    priorities=Counter(x.get("recovery_priority") or recovery_priority(x) for x in recovery)
    return {"bare_form_instruction_unit_count":bare,"orphan_continuation_fragment_count":orphan,"unit_contains_multiple_high_confidence_internal_heading_count":internal,"recovery_required_total_count":len(recovery),"recovery_required_high_priority_content_count":priorities["HIGH"],"recovery_required_medium_priority_content_count":priorities["MEDIUM"],"recovery_required_low_priority_content_count":priorities["LOW"]}
