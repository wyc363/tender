from __future__ import annotations
def run(blocks):
    return sorted([b for b in blocks if b.review_enabled and b.owner_type=="TEXT" and b.text_flow_enabled and b.text_normalized],
                  key=lambda b:(b.page_idx,b.reading_order if b.reading_order is not None else 99999))
