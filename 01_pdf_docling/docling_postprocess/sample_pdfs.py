"""Run real PDF page samples using an installed local Docling environment."""
import argparse
import hashlib
import json
import logging
import time
from pathlib import Path
from .cli import convert


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(filename=args.output_dir/'docling.log',level=logging.INFO,encoding='utf-8')
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    options=PdfPipelineOptions(do_ocr=False,do_table_structure=True)
    options.accelerator_options=AcceleratorOptions(num_threads=4)
    converter=DocumentConverter(format_options={InputFormat.PDF:PdfFormatOption(pipeline_options=options)})
    manifest=json.loads(args.manifest.read_text(encoding='utf-8-sig'))
    summaries=[]
    for case in manifest:
        source=Path(case['source'])
        target=args.output_dir/case['id']
        target.mkdir(parents=True,exist_ok=True)
        digest=hashlib.sha256(source.read_bytes()).hexdigest()
        start=time.monotonic()
        print('[START] '+case['id']+' pages='+str(case['pages']),flush=True)
        try:
            result=converter.convert(source,page_range=tuple(case['pages']))
            result.document.save_as_json(target/'document.json')
            result.document.save_as_markdown(target/'document.md')
            report=convert(target/'document.json',target/'cleaned')
            summary=dict(case,elapsed_seconds=round(time.monotonic()-start,2),
                conversion_status=str(result.status),pdf_sha256=digest,
                pdf_unchanged=digest==hashlib.sha256(source.read_bytes()).hexdigest(),
                do_ocr=False,furniture=report['furniture'],renderer=report['renderer'],checks=report['checks'],
                actual_page_numbers=sorted(json.loads((target/'document.json').read_text(encoding='utf-8'))['pages']),
                warnings=report['warnings'])
            summaries.append(summary)
            print('[DONE] '+case['id']+' '+json.dumps(summary['furniture'])+' seconds='+str(summary['elapsed_seconds']),flush=True)
        except Exception as exc:
            logging.exception('Sample failed: %s',source)
            summaries.append(dict(case,error=str(exc),elapsed_seconds=round(time.monotonic()-start,2)))
            print('[ERROR] '+case['id']+' '+str(exc),flush=True)
        (args.output_dir/'sample_results.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2),encoding='utf-8')
    if any('error' in x for x in summaries): raise SystemExit(1)


if __name__=='__main__': main()
