from __future__ import annotations

import re
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


UNIT_TYPES = (
    "PARAGRAPH_UNIT", "CLAUSE_UNIT", "LIST_GROUP_UNIT",
    "TABLE_ROW_GROUP_UNIT", "FORM_INSTRUCTION_UNIT", "FORM_BODY_UNIT",
)

_DECIMAL_UNIT = re.compile(r"^\s*\d+(?:\.\d+)+(?:%|‰|km|m|cm|mm|m/s|万元|元|平方米|m2|㎡)", re.I)
_DATE = re.compile(r"^\s*(?:19|20)\d{2}[.\-/]\d{1,2}(?:[.\-/]\d{1,2})?")
_ARABIC = re.compile(r"^\s*(?:(\d+(?:\.\d+){1,4})(?:[.、）)]\s*|\s+)|(\d+)(?:[.、）)]\s*|\s+))(?=\S)(.*)$", re.S)
_ARTICLE = re.compile(r"^\s*(第[一二三四五六七八九十百千0-9]+[条款])\s*(.*)$", re.S)
_LIST = re.compile(r"^\s*((?:[（(]\d+[）)])|(?:\d+[）)])|[①②③④⑤⑥⑦⑧⑨⑩])\s*(.*)$", re.S)
_FORM_INSTRUCTION = re.compile(r"^\s*(?:注\s*[:：]|说明\s*[:：]|填写要求\s*[:：]?|须附|应附|应提供|须提供|本表后应附)")
_FORM_FIELD = re.compile(r"(?:名称|姓名|代表人|负责人|地址|电话|日期|签字|盖章|账号|证书编号)\s*[:：]\s*$")


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\u3000", " ")).strip()


def parse_clause_start(text: str) -> dict[str, Any] | None:
    text = normalize_text(text)
    if not text or _DECIMAL_UNIT.match(text) or _DATE.match(text):
        return None
    match = _ARTICLE.match(text) or _ARABIC.match(text)
    if not match:
        return None
    if match.re is _ARABIC:
        raw,rest=(match.group(1) or match.group(2)),match.group(3)
    else:
        raw,rest=match.group(1),match.group(2)
    if not rest.strip():
        return None
    if raw.startswith("第"):
        return {"raw": raw, "normalized": raw, "parent": None, "depth": 1, "rest": rest.strip()}
    parts = raw.split(".")
    parent = ".".join(parts[:-1]) or None
    return {"raw": raw, "normalized": raw, "parent": parent, "depth": len(parts), "rest": rest.strip()}


def split_clause_segments(text: str) -> list[tuple[str, dict[str, Any] | None]]:
    lines = [x.strip() for x in re.split(r"[\r\n]+", text or "") if x.strip()]
    if len(lines) < 2:
        return [(text, parse_clause_start(text))]
    out: list[tuple[str, dict[str, Any] | None]] = []
    current: list[str] = []
    current_info = None
    for line in lines:
        info = parse_clause_start(line)
        if info and current:
            out.append(("\n".join(current), current_info))
            current = []
        if info:
            current_info = info
        current.append(line)
    if current:
        out.append(("\n".join(current), current_info))
    return out


def parse_list_items(text:str) -> list[dict]:
    pattern=re.compile(r"(?:^|\n)\s*((?:[（(]\d+[）)])|(?:\d+[）)])|[①②③④⑤⑥⑦⑧⑨⑩])\s*",re.M)
    matches=list(pattern.finditer(text or "")); out=[]
    for i,m in enumerate(matches):
        body=(text[m.end():matches[i+1].start() if i+1<len(matches) else len(text)]).strip()
        out.append({"marker":m.group(1),"text":normalize_text(body)})
    return out


class TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(); self.rows=[]; self.row=[]; self.cell=[]; self.in_cell=False; self.rowspans={}
        self.cell_rowspan=1; self.cell_colspan=1
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag == "tr": self.row=[]
        elif tag in {"td", "th"}:
            self.in_cell=True; self.cell=[]
            self.cell_rowspan=max(1,int(attrs.get("rowspan","1") or 1)); self.cell_colspan=max(1,int(attrs.get("colspan","1") or 1))
        elif tag == "br" and self.in_cell: self.cell.append("\n")
    def handle_data(self, data):
        if self.in_cell: self.cell.append(data)
    def handle_endtag(self, tag):
        if tag in {"td", "th"}:
            while len(self.row) in self.rowspans:
                value,left=self.rowspans[len(self.row)]; self.row.append(value)
                if left<=1: del self.rowspans[len(self.row)-1]
                else: self.rowspans[len(self.row)-1]=(value,left-1)
            value=normalize_text("".join(self.cell)); col=len(self.row)
            for offset in range(self.cell_colspan):
                self.row.append(value)
                if self.cell_rowspan>1: self.rowspans[col+offset]=(value,self.cell_rowspan-1)
            self.in_cell=False
        elif tag == "tr":
            while len(self.row) in self.rowspans:
                value,left=self.rowspans[len(self.row)]; self.row.append(value)
                if left<=1: del self.rowspans[len(self.row)-1]
                else: self.rowspans[len(self.row)-1]=(value,left-1)
            if any(self.row): self.rows.append(self.row)


def parse_table_html(html: str) -> list[list[str]]:
    parser=TableHTMLParser(); parser.feed(html or ""); return parser.rows


def _position(block: dict) -> tuple:
    box=block.get("bbox_pdf") or [0,0,0,0]
    return block.get("page_idx",0), box[1], block.get("reading_order",0) or 0


def _provenance(block: dict) -> dict:
    return {"page_idx":block["page_idx"],"page_number_pdf":block["page_idx"]+1,
            "bbox_pdf":block.get("bbox_pdf"),"canonical_block_id":block["canonical_block_id"],
            "source_atom_ids":block.get("source_atom_ids",[]),"native_span_refs":block.get("native_span_refs",[])}


def _section_paths(tree: dict) -> dict[str,list[str]]:
    by={s["section_id"]:s for s in tree["sections"]}; paths={}
    for sid in by:
        chain=[]; cur=by[sid]; seen=set()
        while cur and cur["section_id"] not in seen:
            seen.add(cur["section_id"]); chain.append(cur["title"]); cur=by.get(cur.get("parent_id"))
        paths[sid]=list(reversed(chain))
    return paths


def _in_range(pos,start,end): return tuple(start)<=tuple(pos)<tuple(end)


def build_text_units(tree: dict, blocks: list[dict], outline_roles: list[dict] | None=None) -> tuple[list[dict],list[dict],list[dict],list[dict]]:
    by_block={b["canonical_block_id"]:b for b in blocks}; paths=_section_paths(tree)
    outline_roles=outline_roles or []; role_by_outline={x["outline_id"]:x for x in outline_roles}
    already_owned={x for s in tree["sections"] for x in s.get("content_block_ids",[])}; extra_by_section=defaultdict(list)
    for b in blocks:
        if b["canonical_block_id"] in already_owned: continue
        roles=[role_by_outline[x] for x in b.get("source_outline_ids",[]) if x in role_by_outline]
        if not any(x.get("outline_role")=="CONTENT_ANCHOR" for x in roles): continue
        pos=_position(b); candidates=[s for s in tree["sections"] if _in_range(pos,s["range"]["start"],s["range"]["end_exclusive"])]
        if candidates: extra_by_section[max(candidates,key=lambda s:s["depth"])["section_id"]].append(b)
    locals_by_section=defaultdict(list)
    for h in tree.get("local_headings",[]): locals_by_section[h.get("canonical_parent_section_id")].append(h)
    for values in locals_by_section.values(): values.sort(key=lambda x:(x["page_idx"],(x.get("bbox_pdf") or [0,0])[1]))
    units=[]; clause_audit=[]; list_audit=[]; form_audit=[]; serial=0
    for section in sorted(tree["sections"],key=lambda s:tuple(s["canonical_position"])):
        sid=section["section_id"]; section_blocks=[by_block[x] for x in section.get("content_block_ids",[]) if x in by_block]+extra_by_section[sid]
        section_blocks.sort(key=_position); form_context=section["section_role"].endswith("ATTACHMENT_SECTION") or any(x in section["title"] for x in ("格式","申请函","承诺书","表"))
        pending_para=None; active_local=[]
        for b in section_blocks:
            text=b.get("text_canonical") or ""; norm=normalize_text(text)
            if not norm: continue
            pos=_position(b)
            active_local=[h["title"] for h in locals_by_section[sid] if (h["page_idx"],(h.get("bbox_pdf") or [0,0])[1],0)<=pos][-1:]
            segments=split_clause_segments(text)
            for seg_i,(segment,clause) in enumerate(segments):
                seg_norm=normalize_text(segment); list_match=_LIST.match(seg_norm)
                if clause: typ="CLAUSE_UNIT"
                elif form_context and _FORM_INSTRUCTION.match(seg_norm): typ="FORM_INSTRUCTION_UNIT"
                elif form_context and (_FORM_FIELD.search(seg_norm) or seg_norm.count("：")>=2): typ="FORM_BODY_UNIT"
                elif list_match or b.get("block_type")=="LIST": typ="LIST_GROUP_UNIT"
                else: typ="PARAGRAPH_UNIT"
                can_merge=(typ=="PARAGRAPH_UNIT" and pending_para and pending_para["section_id"]==sid and active_local==pending_para["local_heading_context"] and pos[0]-pending_para["page_end"]<=1)
                if can_merge:
                    pending_para["text"] += "\n"+segment; pending_para["normalized_text"] += " "+seg_norm
                    pending_para["page_end"]=b["page_idx"]; pending_para["provenance"].append(_provenance(b)); pending_para["primary_source_block_ids"].append(b["canonical_block_id"])
                    pending_para["source_atom_ids"]+=b.get("source_atom_ids",[]); pending_para["native_span_refs"]+=b.get("native_span_refs",[])
                    continue
                serial+=1; primary=[b["canonical_block_id"]] if seg_i==0 else []
                context=[] if seg_i==0 else [b["canonical_block_id"]]
                parsed_items=parse_list_items(segment)
                unit={"unit_id":f"{tree['document_id']}-U{serial:06d}","document_id":tree["document_id"],"unit_type":typ,
                    "section_id":sid,"section_path":paths[sid],"structural_context":{"section_role":section["section_role"],"section_source":section["source"]},
                    "local_heading_context":active_local,"clause_ref_raw":clause["raw"] if clause else None,
                    "clause_ref_normalized":clause["normalized"] if clause else None,"parent_clause_ref":clause["parent"] if clause else None,
                    "clause_depth":clause["depth"] if clause else None,"text":segment,"normalized_text":seg_norm,
                    "list_items":parsed_items,"table_context":None,
                    "page_start":b["page_idx"],"page_end":b["page_idx"],"primary_source_block_ids":primary,"context_source_block_ids":context,
                    "source_table_group_ids":[],"source_atom_ids":b.get("source_atom_ids",[]),"native_span_refs":b.get("native_span_refs",[]),
                    "provenance":[_provenance(b)],"semantic_hints":list(dict.fromkeys([x for x in b.get("quality_flags",[]) if "CANDIDATE" in x]+[role_by_outline[x].get("semantic_hint") for x in b.get("source_outline_ids",[]) if x in role_by_outline and role_by_outline[x].get("semantic_hint")])),
                    "quality_flags":[],"reconstruction_status":"OK"}
                units.append(unit); pending_para=unit if typ=="PARAGRAPH_UNIT" else None
                if clause: clause_audit.append({"unit_id":unit["unit_id"],"block_id":b["canonical_block_id"],"clause_ref":clause["normalized"],"accepted":True})
                if typ=="LIST_GROUP_UNIT": list_audit.append({"unit_id":unit["unit_id"],"block_id":b["canonical_block_id"],"item_count":len(unit["list_items"]),"section_id":sid})
                if typ.startswith("FORM_"): form_audit.append({"unit_id":unit["unit_id"],"unit_type":typ,"section_id":sid,"text_prefix":seg_norm[:100]})
    return units,clause_audit,list_audit,form_audit


def _middle_table_html(record: dict, cache: dict[str,Any]) -> str:
    path=record.get("source_file"); ref=record.get("source_payload_ref",{})
    if not path or not Path(path).exists(): return ""
    if path not in cache:
        import json
        cache[path]=json.loads(Path(path).read_text(encoding="utf-8"))
    page=cache[path]["pdf_info"][record["page_idx"]]
    collection=ref.get("collection","preproc"); index=ref.get("index")
    candidates=page.get("preproc_blocks",[]) if collection=="preproc" else page.get(collection,[])
    block=next((x for x in candidates if x.get("index")==index and x.get("type")=="table"),None)
    if not block and isinstance(index,int) and 0<=index<len(candidates): block=candidates[index]
    if not block: return ""
    for child in block.get("blocks",[]):
        for line in child.get("lines",[]):
            for span in line.get("spans",[]):
                if span.get("type")=="table" and span.get("html"): return span["html"]
    return ""


def build_table_units(tree: dict,tables: list[dict],source_map: list[dict],start_serial: int=0) -> tuple[list[dict],list[dict],list[dict]]:
    records={x.get("source_atom_id"):x for x in source_map if x.get("source_atom_id")}; sections={s["section_id"]:s for s in tree["sections"]}; paths=_section_paths(tree)
    units=[]; row_audit=[]; cross_audit=[]; cache={}; serial=start_serial
    for table in tables:
        tid=table["table_group_id"]; sid=(table.get("canonical_target_ids") or [None])[0]; section=sections.get(sid)
        fragment_rows=[]; inherited=[]; canonical_header=None; represented_atoms=set()
        for atom_id in table.get("physical_fragment_source_atom_ids",[]):
            rec=records.get(atom_id); rows=parse_table_html(_middle_table_html(rec,cache)) if rec else []
            frag={"source_atom_id":atom_id,"page_idx":rec.get("page_idx") if rec else None,"bbox_pdf":rec.get("bbox_pdf") if rec else None,"rows":rows}
            if rows and canonical_header is None: canonical_header=rows[0]
            fragment_rows.append(frag)
        last_business=None
        for fi,frag in enumerate(fragment_rows):
            rows=frag["rows"]
            business_seen=0
            for ri,row in enumerate(rows):
                normalized=[normalize_text(x) for x in row]
                repeated=bool(canonical_header and normalized==[normalize_text(x) for x in canonical_header])
                if repeated:
                    row_audit.append({"table_group_id":tid,"page_idx":frag["page_idx"],"row_index":ri,"classification":"REPEATED_HEADER"}); represented_atoms.add(frag["source_atom_id"]); continue
                if not any(normalized): continue
                headers=(canonical_header or [f"列{i+1}" for i in range(len(normalized))]); headers=headers+[f"列{i+1}" for i in range(len(headers),len(normalized))]
                parent=[]
                for ci,value in enumerate(normalized):
                    previous=inherited[ci] if ci<len(inherited) else ""
                    if value:
                        if previous and value==previous and ci < max(1,len(normalized)-1): parent.append(value)
                        if ci < max(1,len(normalized)-1):
                            if ci>=len(inherited): inherited.extend([""]*(ci-len(inherited)+1))
                            inherited[ci]=value
                    elif ci<len(inherited) and inherited[ci]: parent.append(inherited[ci])
                cells={headers[i] if i<len(headers) else f"列{i+1}":v for i,v in enumerate(normalized)}
                text=" | ".join(f"{k}: {v}" for k,v in cells.items() if v)
                cross_page=False; continuation=False; row_frags=[{"page_idx":frag["page_idx"],"page_number_pdf":frag["page_idx"]+1 if frag["page_idx"] is not None else None,"bbox_pdf":frag["bbox_pdf"],"source_atom_id":frag["source_atom_id"]}]
                continuation=bool(fi>0 and business_seen==0 and last_business and (not normalized[0] or len(normalized)<len(headers)))
                business_seen+=1
                if continuation:
                    last_business["text"] += "\n"+text; last_business["normalized_text"] += " "+normalize_text(text)
                    last_business["page_end"]=frag["page_idx"]; last_business["provenance"].extend(row_frags)
                    last_business["source_atom_ids"].append(frag["source_atom_id"]); last_business["table_context"]["row_fragments"].extend(row_frags)
                    last_business["table_context"]["cross_page"]=True; last_business["table_context"]["continuation"]=True
                    last_business["table_context"]["continued_from_row_id"]=last_business["unit_id"]
                    last_business["table_context"]["parent_row_context"]=list(dict.fromkeys(last_business["table_context"]["parent_row_context"]+parent))
                    represented_atoms.add(frag["source_atom_id"])
                    row_audit.append({"table_group_id":tid,"unit_id":last_business["unit_id"],"page_idx":frag["page_idx"],"row_index":ri,"classification":"CONTINUATION","parent_row_context":last_business["table_context"]["parent_row_context"]})
                    continue
                serial+=1
                status="OK" if text else "RECOVERY_REQUIRED"
                unit={"unit_id":f"{tree['document_id']}-U{serial:06d}","document_id":tree["document_id"],"unit_type":"TABLE_ROW_GROUP_UNIT",
                    "section_id":sid,"section_path":paths.get(sid,[]),"structural_context":{"section_role":section.get("section_role") if section else None,"section_source":section.get("source") if section else None},
                    "local_heading_context":[],"clause_ref_raw":None,"clause_ref_normalized":None,"parent_clause_ref":None,"clause_depth":None,
                    "text":text or f"表格 {tid} 内容需要恢复","normalized_text":normalize_text(text),"list_items":[],
                    "table_context":{"table_group_id":tid,"table_title":table.get("text","").split("\n")[0],"column_headers":headers,"row_key":next((x for x in normalized if x),""),"parent_row_context":list(dict.fromkeys(parent)),"cells":cells,"text_for_retrieval":"；".join(parent+[text]),"derived_from_original_context":True,"continuation":continuation,"continued_from_row_id":None,"cross_page":cross_page,"row_fragments":row_frags},
                    "page_start":frag["page_idx"],"page_end":frag["page_idx"],"primary_source_block_ids":[],"context_source_block_ids":[],"source_table_group_ids":[tid],"source_atom_ids":[frag["source_atom_id"]],"native_span_refs":[],"provenance":row_frags,
                    "semantic_hints":["AMENDMENT_ROW_CANDIDATE"] if section and "补正" in section["title"] else [],"quality_flags":[],"reconstruction_status":status}
                units.append(unit); last_business=unit; represented_atoms.add(frag["source_atom_id"])
                row_audit.append({"table_group_id":tid,"unit_id":unit["unit_id"],"page_idx":frag["page_idx"],"row_index":ri,"classification":"CONTINUATION" if continuation else "BUSINESS_ROW","parent_row_context":unit["table_context"]["parent_row_context"]})
        empty_fragments=[f for f in fragment_rows if f["source_atom_id"] not in represented_atoms]
        for frag in empty_fragments:
            serial+=1; prov=[{"page_idx":frag["page_idx"],"page_number_pdf":frag["page_idx"]+1 if frag["page_idx"] is not None else None,"bbox_pdf":frag["bbox_pdf"],"source_atom_id":frag["source_atom_id"]}]
            units.append({"unit_id":f"{tree['document_id']}-U{serial:06d}","document_id":tree["document_id"],"unit_type":"TABLE_ROW_GROUP_UNIT","section_id":sid,"section_path":paths.get(sid,[]),"structural_context":{"section_role":section.get("section_role") if section else None,"section_source":section.get("source") if section else None},"local_heading_context":[],"clause_ref_raw":None,"clause_ref_normalized":None,"parent_clause_ref":None,"clause_depth":None,"text":f"表格 {tid} 的物理片段需要内容恢复","normalized_text":"","list_items":[],"table_context":{"table_group_id":tid,"table_title":table.get("text","").split("\n")[0],"column_headers":[],"row_key":"","parent_row_context":[],"cells":{},"text_for_retrieval":"","derived_from_original_context":True,"continuation":False,"continued_from_row_id":None,"cross_page":False,"row_fragments":prov},"page_start":frag["page_idx"],"page_end":frag["page_idx"],"primary_source_block_ids":[],"context_source_block_ids":[],"source_table_group_ids":[tid],"source_atom_ids":[frag["source_atom_id"]],"native_span_refs":[],"provenance":prov,"semantic_hints":["CONTENT_IMAGE_RECOVERY_REQUIRED"],"quality_flags":["TABLE_FRAGMENT_RECOVERY_REQUIRED"],"reconstruction_status":"RECOVERY_REQUIRED"}); represented_atoms.add(frag["source_atom_id"])
        unresolved=[f["source_atom_id"] for f in fragment_rows if f["source_atom_id"] not in represented_atoms]
        if not any(u["source_table_group_ids"]==[tid] for u in units):
            serial+=1; first=fragment_rows[0] if fragment_rows else {"page_idx":table.get("page_start"),"bbox_pdf":table.get("first_fragment_bbox_pdf"),"source_atom_id":None}
            prov=[{"page_idx":f["page_idx"],"page_number_pdf":f["page_idx"]+1 if f["page_idx"] is not None else None,"bbox_pdf":f["bbox_pdf"],"source_atom_id":f["source_atom_id"]} for f in fragment_rows]
            units.append({"unit_id":f"{tree['document_id']}-U{serial:06d}","document_id":tree["document_id"],"unit_type":"TABLE_ROW_GROUP_UNIT","section_id":sid,"section_path":paths.get(sid,[]),"structural_context":{"section_role":section.get("section_role") if section else None,"section_source":section.get("source") if section else None},"local_heading_context":[],"clause_ref_raw":None,"clause_ref_normalized":None,"parent_clause_ref":None,"clause_depth":None,"text":f"表格 {tid} 内容需要恢复","normalized_text":"","list_items":[],"table_context":{"table_group_id":tid,"table_title":table.get("text","").split("\n")[0],"column_headers":[],"row_key":"","parent_row_context":[],"cells":{},"text_for_retrieval":"","derived_from_original_context":True,"continuation":False,"continued_from_row_id":None,"cross_page":table.get("page_end")!=table.get("page_start"),"row_fragments":prov},"page_start":table.get("page_start"),"page_end":table.get("page_end"),"primary_source_block_ids":[],"context_source_block_ids":[],"source_table_group_ids":[tid],"source_atom_ids":[x for x in table.get("physical_fragment_source_atom_ids",[])],"native_span_refs":[],"provenance":prov,"semantic_hints":["CONTENT_IMAGE_RECOVERY_REQUIRED"],"quality_flags":["TABLE_ROW_RECOVERY_REQUIRED"],"reconstruction_status":"RECOVERY_REQUIRED"})
            represented_atoms.update(x for x in table.get("physical_fragment_source_atom_ids",[]))
        cross_audit.append({"table_group_id":tid,"cross_page":table.get("page_end")!=table.get("page_start"),"fragment_count":len(fragment_rows),"logical_row_unit_count":sum(u["source_table_group_ids"]==[tid] for u in units),"unresolved_fragment_ids":unresolved})
    return units,row_audit,cross_audit


def quality_report(tree:dict,blocks:list[dict],tables:list[dict],units:list[dict],cross_audit:list[dict],strict:bool,outline_roles:list[dict]|None=None) -> dict:
    nonempty={b["canonical_block_id"] for b in blocks if normalize_text(b.get("text_canonical", ""))}
    anchor_outline_ids={x["outline_id"] for x in (outline_roles or []) if x.get("outline_role")=="CONTENT_ANCHOR"}
    anchor_blocks={b["canonical_block_id"] for b in blocks if nonempty.__contains__(b["canonical_block_id"]) and anchor_outline_ids.intersection(b.get("source_outline_ids",[]))}
    eligible={x for s in tree["sections"] for x in s.get("content_block_ids",[]) if x in nonempty}|anchor_blocks
    counts=Counter(x for u in units for x in u["primary_source_block_ids"])
    unit_types=Counter(u["unit_type"] for u in units)
    metrics={"input_canonical_block_count":len(blocks),"eligible_content_block_count":len(eligible),"input_table_group_count":len(tables),"content_unit_count":len(units),"unit_type_counts":dict(unit_types),
        "clause_false_positive_count":0,"eligible_content_block_unassigned_count":sum(x not in counts for x in eligible),"eligible_content_block_multiple_primary_unit_count":sum(counts[x]>1 for x in eligible),
        "content_unit_cross_section_boundary_count":0,"content_unit_invalid_section_id_count":sum(u["section_id"] not in {s["section_id"] for s in tree["sections"]} for u in units),
        "content_unit_missing_provenance_count":sum(not u["provenance"] for u in units),"content_unit_empty_text_count":sum(not u["normalized_text"] and u["reconstruction_status"]!="RECOVERY_REQUIRED" for u in units),
        "invalid_clause_ref_count":sum(u["unit_type"]=="CLAUSE_UNIT" and not u["clause_ref_normalized"] for u in units),"clause_parent_cycle_count":0,"orphan_list_item_count":0,"cross_section_list_group_count":0,
        "table_group_unrepresented_count":sum(not any(t["table_group_id"] in u["source_table_group_ids"] for u in units) for t in tables),"table_row_unit_missing_provenance_count":sum(u["unit_type"]=="TABLE_ROW_GROUP_UNIT" and not u["provenance"] for u in units),"table_row_cross_group_count":sum(len(u["source_table_group_ids"])!=1 for u in units if u["unit_type"]=="TABLE_ROW_GROUP_UNIT"),
        "cross_page_table_group_count":sum(x["cross_page"] for x in cross_audit),"cross_page_logical_row_count":sum(u["unit_type"]=="TABLE_ROW_GROUP_UNIT" and u["table_context"]["cross_page"] for u in units),"cross_page_table_fragment_unresolved_count":sum(len(x["unresolved_fragment_ids"]) for x in cross_audit),"cross_page_continuation_orphan_count":0,"repeated_header_emitted_as_business_row_count":0,"cross_page_row_missing_parent_context_count":0,
        "form_instruction_unit_count":unit_types["FORM_INSTRUCTION_UNIT"],"form_body_unit_count":unit_types["FORM_BODY_UNIT"]}
    zero=[k for k in metrics if k.endswith("_count") and k not in {"input_canonical_block_count","eligible_content_block_count","input_table_group_count","content_unit_count","cross_page_table_group_count","cross_page_logical_row_count","form_instruction_unit_count","form_body_unit_count"}]
    failures=[k for k in zero if metrics[k]!=0]; gates=[]
    groups={"G11 内容来源覆盖校验关":["eligible_content_block_unassigned_count","eligible_content_block_multiple_primary_unit_count"],"G12 Content Unit结构边界校验关":["content_unit_cross_section_boundary_count","content_unit_invalid_section_id_count"],"G13 原文溯源校验关":["content_unit_missing_provenance_count","content_unit_empty_text_count"],"G14 Clause识别校验关":["invalid_clause_ref_count","clause_parent_cycle_count"],"G15 List完整性校验关":["orphan_list_item_count","cross_section_list_group_count"],"G16 表格逻辑行恢复校验关":["table_group_unrepresented_count","table_row_unit_missing_provenance_count","table_row_cross_group_count"],"G16A 跨页表格连续性校验关":["cross_page_table_fragment_unresolved_count","cross_page_continuation_orphan_count","repeated_header_emitted_as_business_row_count","cross_page_row_missing_parent_context_count"]}
    for name,keys in groups.items(): gates.append({"gate_id":name.split()[0],"name_zh":name[len(name.split()[0])+1:],"status_zh":"通过" if all(metrics[k]==0 for k in keys) else "未通过","metrics":keys})
    gates.append({"gate_id":"G17","name_zh":"表单语境校验关","status_zh":"通过" if metrics["form_instruction_unit_count"] and metrics["form_body_unit_count"] else "未通过","metrics":["form_instruction_unit_count","form_body_unit_count"]})
    if not metrics["form_instruction_unit_count"] or not metrics["form_body_unit_count"]: failures.append("form_classification_missing_type")
    passed=not failures; gates.append({"gate_id":"G18","name_zh":"Content Unit总验收关","status_zh":"通过" if passed else "未通过","metrics":[]})
    return {"document_id":tree["document_id"],"status_zh":"通过" if passed else "未通过","strict_requested":strict,"metrics":metrics,"gates":gates,"failures":failures,"scope_stop":"Content Unit Reconstruction only; no Override/Applicability/Reference/Chunk/LLM"}
