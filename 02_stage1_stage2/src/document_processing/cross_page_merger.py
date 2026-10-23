from __future__ import annotations
def run(units):
    # Clause builder already retains page-continuing blocks within one logical unit. Keep
    # tables conservatively separated; unsafe cross-page merges are flagged by QA review.
    return sorted(units,key=lambda u:(u.pdf_page_start,u.source_spans[0].bbox[1] if u.source_spans and u.source_spans[0].bbox else 0,u.unit_id))
