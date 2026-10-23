from __future__ import annotations
import re

NOISE={"page_header":"PAGE_HEADER","page_footer":"PAGE_FOOTER","page_number":"PAGE_NUMBER","discarded":"DISCARDED_BLOCK"}
def run(blocks, native, config):
    repeats={}; page_count=max(1,native["page_count"])
    for b in blocks:
        if b.parser_type in {"page_header","page_footer"} and b.text_normalized: repeats[b.text_normalized]=repeats.get(b.text_normalized,0)+1
    for b in blocks:
        flag=NOISE.get(b.parser_type)
        if not b.text_normalized and not b.html: flag="EMPTY_BLOCK"
        if re.fullmatch(r"[—\-\s]*第?\s*\d+\s*页?[—\-\s]*",b.text_normalized): flag="PAGE_NUMBER"
        if repeats.get(b.text_normalized,0)/page_count>=config["repeat_page_ratio"]: flag=flag or "REPEATED_MARGIN_TEXT"
        if flag: b.review_enabled=False; b.text_flow_enabled=False; b.quality_flags.append(flag); b.canonical_region="NOISE"; b.owner_type="NOISE"
    return blocks
