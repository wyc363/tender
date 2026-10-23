from __future__ import annotations
import re

CN = {"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10}
def cn_num(s):
    if s.isdigit(): return int(s)
    if s in CN: return CN[s]
    if "十" in s:
        a,b=s.split("十",1); return (CN.get(a,1)*10)+CN.get(b,0)
    return None

def parse_numbering(text: str) -> dict:
    t="".join(text.strip().split())
    patterns=[
        ("CHAPTER",r"^第([一二三四五六七八九十百\d]+)章",1),
        ("SECTION",r"^第([一二三四五六七八九十百\d]+)节",1),
        ("ATTACHMENT",r"^(?:附件)([一二三四五六七八九十\d]+)",1),
        ("APPENDIX",r"^附录",None),
        ("CHINESE_PAREN",r"^[（(]([一二三四五六七八九十]+)[）)]",1),
        ("CHINESE_DUNHAO",r"^([一二三四五六七八九十]+)、",1),
        ("ARABIC_DECIMAL",r"^(\d+(?:\.\d+)*)[.、]?",1),
    ]
    for family,pat,g in patterns:
        m=re.match(pat,t)
        if m:
            raw=m.group(g) if g else None
            ordinal=cn_num(raw) if raw and not (family=="ARABIC_DECIMAL" and "." in raw) else (int(raw) if raw and raw.isdigit() else None)
            return {"family":family,"ordinal":ordinal,"depth_hint":raw.count(".")+1 if family=="ARABIC_DECIMAL" else None,"prefix":m.group(0)}
    return {"family":"NONE","ordinal":None,"depth_hint":None,"prefix":""}

