import argparse
import hashlib
import json
import random
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from .loader import Document
from .furniture import Config, repair
from .renderer import render, text_markdown


def convert(input_path, output_dir, config=None):
    input_path,output_dir=Path(input_path),Path(output_dir)
    cfg=config or Config()
    before=hashlib.sha256(input_path.read_bytes()).hexdigest()
    doc=Document.load(input_path)
    if not isinstance(doc.data.get('body'),dict) or 'texts' not in doc.data:
        raise ValueError('Input is not a supported Docling document: '+str(input_path))
    decisions,suspicious=repair(doc,cfg)
    clean,debug,entries,stats=render(doc,decisions)
    excluded={x['self_ref'] for x in decisions}
    retained={x['source_ref'] for x in entries}
    inferred=[x for x in decisions if x['source']=='inferred']
    checks=dict(input_unchanged=before==hashlib.sha256(input_path.read_bytes()).hexdigest(),
        no_excluded_refs_rendered=not bool(excluded & retained),
        unique_output_refs=len(retained)==len(entries),
        render_map_valid=all(0<=e['markdown_char_start']<e['markdown_char_end']<=len(clean) for e in entries),
        faithful_text_rendering=all(clean[e['markdown_char_start']:e['markdown_char_end']]==
            '\n'.join(v.rstrip() for v in text_markdown(doc.refs[e['source_ref']]).splitlines()).strip('\n')
            for e in entries if e['source_type']=='texts'),
        list_items_use_unordered_prefix=all(clean[e['markdown_char_start']:e['markdown_char_end']].startswith('- ')
            for e in entries if e['render_type']=='list_item'))
    old_path=input_path.with_suffix('.md')
    old=old_path.read_text(encoding='utf-8') if old_path.exists() else None
    report=dict(input=str(input_path),input_sha256=before,schema_name=doc.data.get('schema_name'),schema_version=doc.data.get('version'),
        **{k:len(doc.data.get(k,[])) for k in ['pages','texts','tables','groups','pictures']},
        config=asdict(cfg),algorithm='document_local_furniture_v2',
        label_distribution=dict(Counter(x.get('label','unknown') for x in doc.data['texts'])),
        furniture=dict(native_excluded=len(decisions)-len(inferred),
            inferred_header_fragments=sum(x['furniture_type']=='page_header_fragment' for x in inferred),
            inferred_footer_fragments=sum(x['furniture_type']=='page_footer_fragment' for x in inferred),
            inference_routes=dict(Counter(x['reasons'][0] for x in inferred)),suspicious_retained=len(suspicious)),
        body_blocks_retained=len(entries),renderer=stats,checks=checks,
        comparison=dict(original_markdown_chars=len(old) if old is not None else None,cleaned_markdown_chars=len(clean)),
        warnings=['heading labels are unverified','Cross-page text/table repair is outside Stage 1–2.',
            'Furniture confidence values are heuristic, not calibrated probabilities.']+doc.warnings,
        complex_table_rendered_lossily=any('complex_table_rendered_lossily=true' in w for w in doc.warnings),
        random_inferred_sample=random.Random(42).sample(inferred,min(20,len(inferred))))
    artifacts={'cleaned.md':clean,'cleaned_debug.md':debug,
        'furniture_decisions.json':dict(document=doc.data.get('name'),total_pages=len(doc.data.get('pages',{})),decisions=decisions,suspicious_candidates=suspicious),
        'render_map.json':dict(offset_unit='Unicode code points; end exclusive',entries=entries),'stage12_report.json':report}
    if any((output_dir/name).resolve()==input_path.resolve() for name in artifacts):
        raise ValueError('Output would overwrite input')
    output_dir.mkdir(parents=True,exist_ok=True)
    for name,value in artifacts.items():
        (output_dir/name).write_text(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser(description='Convert one Docling JSON or recursively convert document.json files in a directory.')
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--config',type=Path,help='Optional JSON overrides for furniture.Config')
    parser.add_argument('--conservative',action='store_true',help='Require native furniture evidence; disable unanchored inference')
    args=parser.parse_args()
    options=json.loads(args.config.read_text(encoding='utf-8-sig')) if args.config else {}
    if args.conservative: options['allow_unanchored']=False
    cfg=Config(**options)
    if args.input.is_file():
        report=convert(args.input,args.output_dir,cfg)
        print(json.dumps({k:report[k] for k in ['furniture','checks','warnings']},ensure_ascii=True,indent=2))
        if not all(report['checks'].values()): raise SystemExit(1)
    elif args.input.is_dir():
        paths=sorted(args.input.rglob('document.json'))
        if not paths: parser.error('No document.json files found')
        results=[]
        for path in paths:
            target=args.output_dir/path.parent.relative_to(args.input)
            try:
                report=convert(path,target,cfg)
                results.append(dict(input=str(path),output=str(target/'cleaned.md'),status='PASS' if all(report['checks'].values()) else 'FAIL',furniture=report['furniture']))
            except (OSError,ValueError,KeyError,TypeError) as exc:
                results.append(dict(input=str(path),status='ERROR',error=str(exc)))
        args.output_dir.mkdir(parents=True,exist_ok=True)
        (args.output_dir/'batch_report.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(Counter(x['status'] for x in results)))
        if any(x['status']!='PASS' for x in results): raise SystemExit(1)
    else: parser.error('Input does not exist')


if __name__=='__main__': main()
