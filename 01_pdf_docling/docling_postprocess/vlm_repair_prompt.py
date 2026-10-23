"""Minimal prompts for risk-gated order/table repair; never request generated source text."""
from __future__ import annotations
import json
from typing import Any
from .deterministic_structure import active_blocks

ORDER_SYSTEM_PROMPT='''你只修复中文 PDF 页面的阅读顺序。只能重排给定 block_id；不得分类、删除、增加或改写内容。返回严格 JSON。'''
TABLE_SYSTEM_PROMPT='''你只识别破碎表格中已有 source block 的逻辑分组与行顺序。只能引用给定 candidate_source_ids；不得生成、抄写或改写正文。每个 candidate_source_id 在整个输出中必须恰好出现一次。每个 table 的 source_ids 与 rows 必须是完全相同的一维唯一 ID 序列，例如 source_ids:["T001","B004","B005"], rows:["T001","B004","B005"]。rows 表示逻辑行顺序，不表示列，绝不能重复 ID 模拟多列。返回严格 JSON。'''

def project(block:dict[str,Any])->dict[str,Any]:
    out={'block_id':block['block_id'],'kind':block['kind'],'bbox_norm':block.get('bbox_norm')}
    out['text_preview']=str(block.get('text',''))[:500]
    return out

def build_order_prompt(page:dict[str,Any])->str:
    return json.dumps({'schema_version':'PF1-order-v1','page_no':page['page_no'],'blocks':[project(b) for b in active_blocks(page)]},ensure_ascii=False,separators=(',',':'))

def build_table_prompt(page:dict[str,Any],candidates:list[dict[str,Any]]|None=None,
                       table_groups:list[dict[str,Any]]|None=None)->str:
    candidates=candidates if candidates is not None else table_candidates(page)
    return json.dumps({'schema_version':'PF1-table-v1','page_no':page['page_no'],
                       'candidate_source_ids':[b['block_id'] for b in candidates],
                       'candidate_blocks':[project(b) for b in candidates],
                       'table_groups':table_groups or [],
                       'hard_constraints':['each candidate_source_id exactly once','source_ids exactly equals rows','rows is a one-dimensional ID array','no free text']},ensure_ascii=False,separators=(',',':'))

def table_candidates(page:dict[str,Any])->list[dict[str,Any]]:
    blocks=active_blocks(page);positions=[i for i,b in enumerate(blocks) if b.get('kind')=='table']
    return blocks[min(positions):] if positions else blocks
