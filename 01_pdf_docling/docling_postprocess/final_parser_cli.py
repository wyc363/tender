"""Phase PF-2 frozen Final Parser full-document CLI."""
from __future__ import annotations
import argparse,hashlib,json
from pathlib import Path
from .goodmd_renderer import chunk_preflight,render_goodmd
from .pf2_pipeline import build_reports,check_only,code_fingerprint,run_full,write_audit_html,write_good_preview
from .vlm_cli import _validate_a1_inputs
from .vlm_config import load_parser_vlm_settings,validate_parser_vlm_settings
from .vlm_structure import read_jsonl,write_json

def sha(path:Path)->str:return hashlib.sha256(path.read_bytes()).hexdigest().upper()

def main()->None:
 p=argparse.ArgumentParser(description='PF-2 frozen Final Parser: 169 active pages and gated Good Markdown.')
 p.add_argument('--pdf',type=Path,required=True);p.add_argument('--json',type=Path,required=True);p.add_argument('--a1-dir',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);p.add_argument('--env-file',type=Path);p.add_argument('--all-active-pages',action='store_true',required=True);p.add_argument('--no-resume',action='store_true')
 g=p.add_mutually_exclusive_group(required=True);g.add_argument('--check-only',action='store_true');g.add_argument('--run',action='store_true');a=p.parse_args()
 pdf=a.pdf.resolve();doc=a.json.resolve();a1=a.a1_dir.resolve();out=a.output_dir.resolve();_validate_a1_inputs(a1,pdf,doc)
 settings=load_parser_vlm_settings(a.env_file);validate_parser_vlm_settings(settings,require_api=True)
 pdf_sha=sha(pdf);json_sha=sha(doc);report=check_only(pdf,doc,a1,out,settings,pdf_sha,json_sha)
 if a.check_only:
  print(json.dumps(report,ensure_ascii=False,indent=2));raise SystemExit(0 if report['status']=='PASS' else 1)
 if report['status']!='PASS':raise SystemExit('PF2 check-only failed; no API calls were made')
 records=read_jsonl(a1/'page_blocks.jsonl');manifest=json.loads((a1/'page_images'/'manifest.json').read_text(encoding='utf8'));code=code_fingerprint()
 input_fp=hashlib.sha256((pdf_sha+json_sha+code['combined_sha256']+settings.model+str(settings.max_pixels)).encode()).hexdigest().upper()
 finals,routes,calls,reused=run_full(records,manifest,out,settings,input_fp,not a.no_resume)
 full,router,manual=build_reports(records,finals,routes,calls,reused,out,settings,input_fp)
 images={int(x['page_no']):x['path'] for x in manifest['images']};write_audit_html(records,finals,routes,images,out)
 conclusion='NEEDS_PF2_FIX'
 # Human review is a real PF-2 gate. Never emit formal Markdown before a
 # fingerprint-matched PASS report exists for the sampled pages.
 manual_path=out/'PF2_Manual_Review_Result.json';manual_pass=False
 if manual_path.is_file():
  manual=json.loads(manual_path.read_text(encoding='utf8'))
  manual_pass=manual.get('status')=='PASS' and manual.get('input_fingerprint')==input_fp
 if full['status']=='PASS' and manual_pass:
  maps,validation=render_goodmd(records,finals,out)
  preflight=chunk_preflight((out/'good.md').read_text(encoding='utf8'),finals);preflight['ready_for_chunker']=preflight['ready_for_chunker'] and validation['status']=='PASS';write_json(out/'PF2_Chunk_Preflight.json',preflight)
  write_good_preview(records,finals,out)
  if validation['status']=='PASS' and preflight['ready_for_chunker']:conclusion='READY_FOR_MD_CHUNK'
 write_json(out/'PF2_Final_Result.json',{'conclusion':conclusion,'parser_automatic_gate':full['status'],'manual_gate':'PASS' if manual_pass else 'NOT_PASSED','goodmd_gate':(json.loads((out/'PF2_GoodMD_Validation.json').read_text(encoding='utf8'))['status'] if (out/'PF2_GoodMD_Validation.json').exists() else 'NOT_RUN'),'chunker_executed':False,'retrieval_executed':False,'judge_executed':False})
 print(json.dumps({'conclusion':conclusion,'active_pages':len(finals),'vlm_pages':router['unique_vlm_pages'],'vlm_attempts':full['vlm_attempts'],'input_tokens':full['input_tokens'],'output_tokens':full['output_tokens'],'estimated_cost_cny':full['estimated_cost'].get('estimated_cost'),'output_dir':str(out)},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
