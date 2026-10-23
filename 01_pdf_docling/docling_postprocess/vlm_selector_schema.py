"""Strict A2.2 selector schema and semantic validation."""

from __future__ import annotations

from typing import Any

from .vlm_schema import parse_json_response
from .vlm_selector_prompt import SCHEMA_VERSION

PAGE_TYPES = {"content", "toc", "form", "mixed"}

SELECTOR_JSON_SCHEMA = {"type":"object","additionalProperties":False,
 "required":["schema_version","page_no","page_type","elements"],
 "properties":{"schema_version":{"type":"string","const":SCHEMA_VERSION},
  "page_no":{"type":"integer","minimum":1},
  "page_type":{"type":"string","enum":["content","toc","form","mixed"]},
  "elements":{"type":"array","items":{"type":"object","additionalProperties":False,
   "required":["block_id","chunk_boundary"],"properties":{"block_id":{"type":"string"},
    "chunk_boundary":{"type":"boolean"}}}}}}


def selector_schema_for(active: list[dict[str, Any]]) -> dict[str, Any]:
    variants=[]
    for block in active:
        boundary={"type":"boolean","const":False} if block.get("kind")=="table" else {"type":"boolean"}
        variants.append({"type":"object","additionalProperties":False,
          "required":["block_id","chunk_boundary"],"properties":{
            "block_id":{"type":"string","const":block["block_id"]},"chunk_boundary":boundary}})
    schema={**SELECTOR_JSON_SCHEMA,"properties":dict(SELECTOR_JSON_SCHEMA["properties"])}
    schema["properties"]["elements"]={"type":"array","minItems":len(active),"maxItems":len(active),
                                         "items":{"oneOf":variants}}
    return schema


def validate_selector(value: dict[str,Any], requested_page:int, active:list[dict[str,Any]],
                      covered_ids:set[str]|None=None, finish_reason:str|None=None)->list[str]:
    errors=[]; required={"schema_version","page_no","page_type","elements"}
    if finish_reason=="length": errors.append("INVALID_TRUNCATED: finish_reason=length")
    if set(value)-required: errors.append("EXTRA_FIELDS: "+",".join(sorted(set(value)-required)))
    if required-set(value): errors.append("MISSING_FIELDS: "+",".join(sorted(required-set(value))))
    if value.get("schema_version")!=SCHEMA_VERSION: errors.append("SCHEMA_VERSION_INVALID")
    if type(value.get("page_no")) is not int or value.get("page_no")!=requested_page:
        errors.append(f"PAGE_NO_MISMATCH: expected {requested_page}, got {value.get('page_no')}")
    if value.get("page_type") not in PAGE_TYPES: errors.append("PAGE_TYPE_INVALID")
    elements=value.get("elements")
    if not isinstance(elements,list): errors.append("ELEMENTS_NOT_ARRAY"); elements=[]
    by_id={b["block_id"]:b for b in active}; active_ids=set(by_id); covered_ids=covered_ids or set(); ids=[]
    for i,e in enumerate(elements):
        if not isinstance(e,dict): errors.append(f"ELEMENT_{i}_NOT_OBJECT"); continue
        if set(e)!={"block_id","chunk_boundary"}: errors.append(f"ELEMENT_{i}_FIELDS_INVALID")
        block_id=e.get("block_id"); boundary=e.get("chunk_boundary")
        if not isinstance(block_id,str): errors.append(f"ELEMENT_{i}_BLOCK_ID_INVALID"); continue
        ids.append(block_id)
        if type(boundary) is not bool: errors.append(f"CHUNK_BOUNDARY_NOT_BOOLEAN: {block_id}")
        if block_id in by_id and by_id[block_id].get("kind")=="table" and boundary is not False:
            errors.append(f"TABLE_BOUNDARY_TRUE: {block_id}")
        if value.get("page_type")=="toc" and boundary is not False:
            errors.append(f"TOC_BOUNDARY_TRUE: {block_id}")
    id_set=set(ids)
    if len(ids)!=len(id_set): errors.append("ELEMENTS_DUPLICATE")
    if id_set-active_ids: errors.append("ELEMENTS_UNKNOWN: "+",".join(sorted(id_set-active_ids)))
    if id_set & covered_ids: errors.append("COVERED_BY_TABLE_RETURNED: "+",".join(sorted(id_set&covered_ids)))
    if active_ids-id_set: errors.append("ELEMENTS_MISSING: "+",".join(sorted(active_ids-id_set)))
    return errors
