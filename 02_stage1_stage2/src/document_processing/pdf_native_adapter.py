from __future__ import annotations
from pathlib import Path
import fitz

def load(pdf_path: Path, mineru_blocks) -> dict:
    doc=fitz.open(pdf_path); by_page={}
    for b in mineru_blocks: by_page.setdefault(b.page_idx,[]).append(b)
    pages=[]
    for i,p in enumerate(doc):
        native=p.get_text("text"); mineru=sum(len(b.text_normalized) for b in by_page.get(i,[]))
        tables=[]
        try: tables=[list(t.bbox) for t in p.find_tables().tables]
        except Exception: pass
        pages.append({"page_idx":i,"pdf_page":i+1,"width":p.rect.width,"height":p.rect.height,"page_mode":"NATIVE_TEXT" if native.strip() else "OCR",
                      "native_char_count":len(native.strip()),"mineru_char_count":mineru,"native_table_count":len(tables),"native_table_bboxes":tables})
    toc=doc.get_toc(simple=False); doc.close(); return {"pages":pages,"toc":toc,"page_count":len(pages)}
