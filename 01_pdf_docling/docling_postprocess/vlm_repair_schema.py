"""Strict schemas and lossless validators for PF-1 repairs."""
from __future__ import annotations
import json,re
from typing import Any

def order_schema(ids:list[str])->dict[str,Any]:
 return {'type':'object','additionalProperties':False,'required':['schema_version','page_no','ordered_block_ids'],'properties':{'schema_version':{'const':'PF1-order-v1'},'page_no':{'type':'integer'},'ordered_block_ids':{'type':'array','minItems':len(ids),'maxItems':len(ids),'items':{'type':'string','enum':ids}}}}

def table_schema(ids:list[str])->dict[str,Any]:
 return {'type':'object','additionalProperties':False,'required':['schema_version','page_no','tables'],'properties':{'schema_version':{'const':'PF1-table-v1'},'page_no':{'type':'integer'},'tables':{'type':'array','minItems':1,'items':{'type':'object','additionalProperties':False,'required':['source_ids','rows'],'properties':{'source_ids':{'type':'array','minItems':1,'items':{'type':'string','enum':ids}},'rows':{'type':'array','minItems':1,'items':{'type':'string','enum':ids}}}}}}}

def parse(text:str)->tuple[dict[str,Any]|None,list[str]]:
 try:
  value=json.loads(text)
  return (value,[]) if isinstance(value,dict) else (None,['ROOT_NOT_OBJECT'])
 except json.JSONDecodeError as e:return None,[f'JSON_INVALID: {e.msg}']

def validate_order(value:dict[str,Any],page_no:int,ids:list[str])->list[str]:
 errors=[]
 if set(value)!={'schema_version','page_no','ordered_block_ids'}:errors.append('FIELDS_INVALID')
 if value.get('schema_version')!='PF1-order-v1':errors.append('SCHEMA_VERSION_INVALID')
 if value.get('page_no')!=page_no:errors.append('PAGE_NO_INVALID')
 order=value.get('ordered_block_ids')
 if not isinstance(order,list): return errors+['ORDER_NOT_ARRAY']
 if len(order)!=len(set(order)):errors.append('ORDER_DUPLICATE')
 if set(order)!=set(ids):errors.append('ORDER_NOT_EXACT_ACTIVE_SET')
 return errors

def validate_table(value:dict[str,Any],page_no:int,ids:list[str])->list[str]:
 errors=[]
 if set(value)!={'schema_version','page_no','tables'}:errors.append('FIELDS_INVALID')
 if value.get('schema_version')!='PF1-table-v1':errors.append('SCHEMA_VERSION_INVALID')
 if value.get('page_no')!=page_no:errors.append('PAGE_NO_INVALID')
 tables=value.get('tables')
 if not isinstance(tables,list):return errors+['TABLES_NOT_ARRAY']
 used=[]
 for i,t in enumerate(tables):
  if not isinstance(t,dict) or set(t)!={'source_ids','rows'}:errors.append(f'TABLE_{i}_FIELDS_INVALID');continue
  src=t.get('source_ids');rows=t.get('rows')
  if not isinstance(src,list) or not isinstance(rows,list):errors.append(f'TABLE_{i}_SHAPE_INVALID');continue
  flat=rows
  if any(not isinstance(x,str) for x in src+flat):errors.append(f'TABLE_{i}_SHAPE_INVALID');continue
  if any(x not in ids for x in src+flat):errors.append(f'TABLE_{i}_UNKNOWN_ID')
  if flat!=src:errors.append(f'TABLE_{i}_ROWS_SOURCE_MISMATCH')
  if len(flat)!=len(set(flat)):errors.append(f'TABLE_{i}_DUPLICATE_ROWS')
  if len(src)!=len(set(src)):errors.append(f'TABLE_{i}_DUPLICATE_SOURCE')
  used+=src
 if len(used)!=len(set(used)):errors.append('TABLE_REPAIRS_OVERLAP')
 if set(used)!=set(ids):errors.append('TABLE_CANDIDATES_NOT_EXACTLY_ONCE')
 return errors
