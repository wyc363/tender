"""Conservative risk signals that gate Parser Final VLM repair calls."""
from __future__ import annotations
import re
from typing import Any
from .deterministic_structure import PURE_NUMBER_RE, active_blocks

def detect_order_risk(page: dict[str, Any]) -> dict[str, Any]:
    blocks=active_blocks(page); reasons=[]
    if any(PURE_NUMBER_RE.match(str(b.get('text','')).strip()) for b in blocks): reasons.append('ISOLATED_NUMBER_TITLE_CODE_REPAIR')
    placeholders=sum(str(b.get('text','')).strip() in {'/。','/','。'} for b in blocks)
    enumerated=sum(bool(re.match(r'^[一二三四五六七八九十]+[、．.]',str(b.get('text','')).strip())) for b in blocks)
    if placeholders>=2 and enumerated>=2: reasons.append('REPEATED_TEMPLATE_PLACEHOLDERS')
    ys=[(b.get('bbox_norm') or [0,0,0,0])[1] for b in blocks]
    if any(ys[i]>ys[i+1]+.03 for i in range(len(ys)-1)): reasons.append('A1_Y_INVERSION')
    # Same vertical bands split into stable left/right columns are ambiguous in document order.
    column_pairs=0
    for i,a in enumerate(blocks):
        ba=a.get('bbox_norm') or [0,0,0,0]
        for b in blocks[i+1:]:
            bb=b.get('bbox_norm') or [0,0,0,0]
            overlap=max(0,min(ba[3],bb[3])-max(ba[1],bb[1]))
            long_content=max(len(str(a.get('text','')).strip()),len(str(b.get('text','')).strip()))>=30
            if overlap>.008 and long_content and (ba[2] < bb[0]-.04 or bb[2] < ba[0]-.04): column_pairs+=1
    if column_pairs>=4: reasons.append('MULTI_COLUMN_LAYOUT')
    needs_vlm=any(x in reasons for x in ('REPEATED_TEMPLATE_PLACEHOLDERS','A1_Y_INVERSION','MULTI_COLUMN_LAYOUT'))
    return {'risk':bool(reasons),'needs_vlm':needs_vlm,'reasons':reasons}

def detect_table_risk(page: dict[str, Any]) -> dict[str, Any]:
    blocks=active_blocks(page); tables=[b for b in blocks if b.get('kind')=='table']; reasons=[]
    if len(tables)>=3:
        first=min((b.get('bbox_norm') or [0,1])[1] for b in tables)
        last=max((b.get('bbox_norm') or [0,0,0,0])[3] for b in tables)
        between=[b for b in blocks if b.get('kind')=='text' and first <= (b.get('bbox_norm') or [0,0])[1] <= last+.1]
        if len(between)>=4: reasons.append('TABLE_TEXT_INTERLEAVING')
    for b in tables:
        html=str(b.get('table_html',''))
        if not html or '<table' not in html.lower() or html.lower().count('<tr')==0: reasons.append('MALFORMED_TABLE_HTML')
    return {'risk':bool(reasons),'needs_vlm':bool(reasons),'reasons':list(dict.fromkeys(reasons))}
