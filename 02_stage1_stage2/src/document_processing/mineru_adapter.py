from __future__ import annotations
import hashlib, json, re
from pathlib import Path
from typing import Any
from .schemas import UnifiedBlock

def _norm(s: str) -> str: return re.sub(r"\s+", " ", s or "").strip()
def _texts(value: Any) -> list[str]:
    if isinstance(value, dict):
        out = [str(value.get("content", ""))] if value.get("type") == "text" else []
        for v in value.values(): out += _texts(v)
        return out
    if isinstance(value, list):
        out=[]
        for v in value: out += _texts(v)
        return out
    return []
def _iou(a,b):
    if not a or not b:return 0.0
    x1,y1,x2,y2=max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3]); inter=max(0,x2-x1)*max(0,y2-y1)
    aa=max(0,a[2]-a[0])*max(0,a[3]-a[1]); bb=max(0,b[2]-b[0])*max(0,b[3]-b[1])
    return inter/(aa+bb-inter) if aa+bb-inter else 0

def deduplicate_source_blocks(blocks: list[UnifiedBlock]) -> list[UnifiedBlock]:
    kept=[]
    for b in blocks:
        match=next((x for x in kept if x.page_idx==b.page_idx and _iou(x.bbox,b.bbox)>.75 and hashlib.sha1(x.text_normalized.encode()).hexdigest()==hashlib.sha1(b.text_normalized.encode()).hexdigest()),None)
        if match: match.alternate_sources.append(b.source_file)
        else: kept.append(b)
    return kept

def load(doc_id: str, raw_dir: Path) -> tuple[list[UnifiedBlock], dict[str,int]]:
    v2=next(raw_dir.glob("*_content_list_v2.json")); pages=json.loads(v2.read_text(encoding="utf-8")); blocks=[]; stats={"v2_html":0,"middle_fallback":0,"content_list_fallback":0,"unresolved":0}
    for page_idx,page in enumerate(pages):
        for order,item in enumerate(page):
            typ=item.get("type","unknown"); content=item.get("content",{}); html=content.get("html") if isinstance(content,dict) else None
            text="\n".join(dict.fromkeys(t for t in _texts(content) if t));
            if typ=="table" and html:
                stats["v2_html"]+=1
                if not text:
                    text=_norm(re.sub(r"<[^>]+>"," ",html))
            bid=f"{doc_id}-p{page_idx:04d}-b{order:04d}"
            blocks.append(UnifiedBlock(block_id=bid,doc_id=doc_id,page_idx=page_idx,pdf_page=page_idx+1,bbox=item.get("bbox"),reading_order=order,
                parser_type=typ,text_raw=text,text_normalized=_norm(text),html=html,source_file=str(v2),source_block_ids=[bid]))
    return deduplicate_source_blocks(blocks),stats
