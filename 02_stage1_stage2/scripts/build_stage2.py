from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/"src"))
from document_processing.pipeline import build_stage2_document
def main():
    p=argparse.ArgumentParser(); p.add_argument("--doc-id",required=True); p.add_argument("--pdf",type=Path,required=True); p.add_argument("--mineru-raw",type=Path,required=True); p.add_argument("--output",type=Path,required=True); p.add_argument("--section-map",type=Path)
    a=p.parse_args(); cfg=yaml.safe_load((ROOT/"configs/document_processing.yaml").read_text(encoding="utf-8")); result=build_stage2_document(a.doc_id,a.pdf,a.mineru_raw,a.output,cfg,a.section_map); print(json.dumps(result,ensure_ascii=False,indent=2)); return 0
if __name__=="__main__": raise SystemExit(main())
