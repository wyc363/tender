from __future__ import annotations
import re,unicodedata
from difflib import SequenceMatcher
from typing import Any

def normalize_text(text: str) -> str:
    text=unicodedata.normalize("NFKC",text or "").replace("（","(").replace("）",")")
    text=re.sub(r"\s+","",text)
    return text.strip()

def extract_text(value: Any) -> str:
    out=[]
    def walk(x):
        if isinstance(x,dict):
            if x.get("type") in {"text","inline_equation","interline_equation"} and isinstance(x.get("content"),str): out.append(x["content"])
            for v in x.values(): walk(v)
        elif isinstance(x,list):
            for v in x: walk(v)
    walk(value); return "\n".join(dict.fromkeys(x for x in out if x))

def middle_text(block: dict) -> str:
    return "".join(str(span.get("content","")) for line in block.get("lines",[]) for span in line.get("spans",[]) if span.get("content"))

def similarity(a: str,b: str) -> float:
    a,b=normalize_text(a),normalize_text(b)
    if not a or not b:return 0.0
    if a==b:return 1.0
    if a in b or b in a:return min(len(a),len(b))/max(len(a),len(b))
    return SequenceMatcher(None,a,b).ratio()
