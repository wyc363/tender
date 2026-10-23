from __future__ import annotations
from pathlib import Path
import fitz

def build(doc_id: str,pdf_path: Path) -> tuple[list[dict],list[list[dict]]]:
    doc=fitz.open(pdf_path); registry=[]; native_pages=[]
    for idx,page in enumerate(doc):
        raw=page.get_text("dict"); lines=[]
        for block_no,b in enumerate(raw.get("blocks",[])):
            if b.get("type")!=0:continue
            for line_no,line in enumerate(b.get("lines",[])):
                spans=line.get("spans",[]); text="".join(s.get("text","") for s in spans)
                if not text.strip():continue
                bbox=list(line.get("bbox") or spans[0].get("bbox")); sizes=[float(s.get("size",0)) for s in spans]
                lines.append({"native_ref":f"{doc_id}-N-P{idx:06d}-L{len(lines):04d}","page_idx":idx,"text":text,"bbox_pdf":bbox,
                              "font_size_max":max(sizes or [0]),"font_size_avg":sum(sizes)/len(sizes) if sizes else 0,"block_no":block_no,"line_no":line_no})
        rect=page.rect
        registry.append({"doc_id":doc_id,"page_uid":f"{doc_id}-P{idx:06d}","page_idx":idx,"pdf_page_no":idx+1,"width_pt":rect.width,"height_pt":rect.height,
                         "rotation":page.rotation,"mediabox":list(page.mediabox),"cropbox":list(page.cropbox),"native_page_label":page.get_label() or None,
                         "native_text_char_count":sum(len(x["text"]) for x in lines)})
        native_pages.append(lines)
    doc.close(); return registry,native_pages
