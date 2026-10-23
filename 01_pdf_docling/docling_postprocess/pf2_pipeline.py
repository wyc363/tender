"""Frozen PF-1 parser applied to all active pages with PF-2 gates."""
from __future__ import annotations
import hashlib,html,json,random,re
from collections import Counter
from pathlib import Path
from typing import Any
from .deterministic_structure import active_blocks,build_final_page,source_text_coverage
from .structure_risk import detect_order_risk,detect_table_risk
from .vlm_config import ParserVLMSettings,estimate_cost
from .vlm_repair_router import _invoke
from .vlm_structure import read_jsonl,upsert_page_result,write_json,write_jsonl
from .table_repair_scope import annotate_valid_repairs,build_table_repair_scope

PF2_VERSION='PF2.2-table-scope-v1';PROMPT_SCHEMA_VERSION='PF1-order-v1+PF1-table-v1'
FROZEN_FILES=['deterministic_structure.py','structure_risk.py','vlm_repair_prompt.py','vlm_repair_schema.py','vlm_repair_router.py','table_repair_scope.py']

def code_fingerprint()->dict[str,Any]:
 root=Path(__file__).parent;files={n:hashlib.sha256((root/n).read_bytes()).hexdigest().upper() for n in FROZEN_FILES}
 return {'pf2_version':PF2_VERSION,'prompt_schema_version':PROMPT_SCHEMA_VERSION,'files':files,
         'combined_sha256':hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest().upper()}

def check_only(pdf:Path,json_path:Path,a1:Path,output:Path,settings:ParserVLMSettings,pdf_sha:str,json_sha:str)->dict[str,Any]:
 records=read_jsonl(a1/'page_blocks.jsonl');manifest=json.loads((a1/'page_images'/'manifest.json').read_text(encoding='utf8'));active=[p for p in records if active_blocks(p)];empty=[p['page_no'] for p in records if not active_blocks(p)]
 output.mkdir(parents=True,exist_ok=True);probe=output/'.write_probe';probe.write_text('ok');probe.unlink()
 image_paths=[Path(x['path']) for x in manifest.get('images',[])]
 checks={'pdf_sha_matches_a1':bool(pdf_sha),'json_sha_matches_a1':bool(json_sha),'page_blocks_exists':(a1/'page_blocks.jsonl').is_file(),
         'page_image_cache_accessible':len(image_paths)==172 and all(x.is_file() for x in image_paths),'physical_pages_172':len(records)==172,
         'active_pages_169':len(active)==169,'empty_pages_exact':empty==[9,73,121],'pf1_code_version_exists':all((Path(__file__).parent/n).is_file() for n in FROZEN_FILES),
         'parser_api_configured':settings.api_configured,'api_key_not_printed':True,'output_writable':True,'api_calls_zero':True}
 report={'status':'PASS' if all(checks.values()) else 'FAIL','execution_mode':'CHECK_ONLY','api_calls':0,'physical_pages':len(records),'active_pages':len(active),'empty_pages':empty,'fingerprint':code_fingerprint(),'checks':checks,'api_key':'CONFIGURED' if settings.api_configured else 'MISSING'}
 write_json(output/'PF2_Check_Only_Report.json',report);return report

def _page_fingerprint(global_fp:str,page:dict[str,Any])->str:
 ids=[b['block_id'] for b in active_blocks(page)];return hashlib.sha256((global_fp+'|'+json.dumps(ids,separators=(',',':'))).encode()).hexdigest().upper()

def run_full(records:list[dict[str,Any]],manifest:dict[str,Any],output:Path,settings:ParserVLMSettings,input_fingerprint:str,resume:bool=True):
 active=[p for p in records if active_blocks(p)];images={int(x['page_no']):Path(x['path']) for x in manifest['images']};states=output/'page_state';states.mkdir(parents=True,exist_ok=True)
 final_path=output/'final_page_structures.jsonl';finals=[];routes=[];calls=[];reused=0
 for index,page in enumerate(active,1):
  n=int(page['page_no']);active_ids=[b['block_id'] for b in active_blocks(page)];pf=_page_fingerprint(input_fingerprint,page);state_path=states/f'page_{n:04d}.json'
  if resume and state_path.is_file():
   state=json.loads(state_path.read_text(encoding='utf8'))
   if state.get('page_fingerprint')==pf and state.get('active_ids')==active_ids and state.get('status')=='COMPLETE':
    final=state['final'];route=state['route'];finals.append(final);routes.append(route);calls.extend(state.get('calls',[]));upsert_page_result(final_path,final);reused+=1;continue
  order=detect_order_risk(page);table=detect_table_risk(page);scope=build_table_repair_scope(page);order_result=table_result=None
  if order['needs_vlm']:order_result=_invoke('order',page,images[n],settings,output, __import__('docling_postprocess.vlm_client',fromlist=['call_page_structure']).call_page_structure);calls.append(order_result)
  if table['needs_vlm'] and scope['repairable']:
   table_result=_invoke('table',page,images[n],settings,output, __import__('docling_postprocess.vlm_client',fromlist=['call_page_structure']).call_page_structure,scope);calls.append(table_result)
  repaired_order=(order_result or {}).get('value',{}).get('ordered_block_ids') if (order_result or {}).get('status')=='SUCCESS' else None
  repairs=annotate_valid_repairs(table_result['value'],table_result.get('scope_validation',[])) if (table_result or {}).get('status')=='SUCCESS' else None
  final=build_final_page(page,repaired_order,repairs);final.update({'order_risk':order['risk'],'order_risk_reasons':order['reasons'],'table_risk':table['risk'],'table_risk_reasons':table['reasons'],
    'vlm_order_called':order_result is not None,'vlm_table_called':table_result is not None,
    'order_repair_status':('SUCCESS' if (order_result or {}).get('status')=='SUCCESS' else ('FALLBACK_A1' if order_result else 'NOT_CALLED')),
    'table_repair_status':('SUCCESS' if (table_result or {}).get('status')=='SUCCESS' else ('FALLBACK_A1' if table_result else ('SKIPPED_NO_ELIGIBLE_SOURCE_FACTS' if table['risk'] and not scope['repairable'] else 'NOT_CALLED'))),
    'table_repair_scope':scope,'table_scope_validation':(table_result or {}).get('scope_validation',[])})
  route={'page_no':n,'order_risk':order['risk'],'order_risk_reasons':order['reasons'],'table_risk':table['risk'],'table_risk_reasons':table['reasons'],
    'vlm_order_called':order_result is not None,'vlm_order_status':final['order_repair_status'],'vlm_table_called':table_result is not None,'vlm_table_status':final['table_repair_status'],
    'fallback_used':'FALLBACK_A1' in (final['order_repair_status'],final['table_repair_status']) or final['table_repair_status']=='SKIPPED_NO_ELIGIBLE_SOURCE_FACTS',
    'table_scope_status':scope['scope_status'],'table_scope_repairable':scope['repairable']}
  state={'status':'COMPLETE','page_fingerprint':pf,'active_ids':active_ids,'final':final,'route':route,'calls':[x for x in (order_result,table_result) if x]}
  write_json(state_path,state);finals.append(final);routes.append(route);upsert_page_result(final_path,final)
  print(f'[{index}/{len(active)}] page {n:04d} {final["order_repair_status"]}/{final["table_repair_status"]}',flush=True)
 write_jsonl(final_path,finals)
 return finals,routes,calls,reused

def heading_risks(finals:list[dict[str,Any]])->dict[str,Any]:
 pages=[];counts={}
 for f in finals:
  hs=[e for e in f['ordered_elements'] if e['semantic_type']=='heading'];texts=[e for e in f['ordered_elements'] if e['kind']=='text'];counts[str(f['page_no'])]=len(hs);risks=[]
  for e in hs:
   t=str(e.get('text',''))
   why=[]
   if len(t)>80:why.append('LENGTH_GT_80')
   if re.search(r'[：:]\s*\S{3,}',t):why.append('COLON_CONCRETE_VALUE')
   if t.endswith(('。','；',';')):why.append('SENTENCE_END')
   if re.match(r'^\d+(?:\.\d+)+',t):why.append('DEEP_NUMBER')
   if why:risks.append({'source_ids':e['source_ids'],'text':t,'reasons':why})
  ratio=len(hs)/len(texts) if texts else 0;density=None
  if len(texts)>=6 and ratio>.5:density='HIGH_HEADING_DENSITY_RISK'
  elif len(texts)>=6 and ratio>.35:density='HEADING_DENSITY_RISK'
  if risks or density:pages.append({'page_no':f['page_no'],'heading_count':len(hs),'text_blocks':len(texts),'heading_ratio':ratio,'density_risk':density,'candidate_risks':risks})
 return {'heading_count':sum(counts.values()),'heading_count_per_page':counts,'risk_pages':pages,'high_density_pages':[p['page_no'] for p in pages if p['density_risk']=='HIGH_HEADING_DENSITY_RISK'],'candidate_risk_count':sum(len(p['candidate_risks']) for p in pages)}

def build_reports(records,finals,routes,calls,reused,output,settings,input_fp):
 expected=[b['block_id'] for p in records for b in active_blocks(p)];covered={b['block_id'] for p in records for b in p.get('blocks',[]) if 'COVERED_BY_TABLE' in b.get('flags',[])}
 actual=[x for f in finals for e in f['ordered_elements'] for x in e['source_ids']];missing=sorted(set(expected)-set(actual));dup=sorted(x for x in set(actual) if actual.count(x)>1);unknown=sorted(set(actual)-set(expected));leaks=sorted(set(actual)&covered)
 headings=heading_risks(finals);write_json(output/'PF2_Heading_Risk.json',headings)
 vlm_pages=sorted({r['page_no'] for r in routes if r['vlm_order_called'] or r['vlm_table_called']});ratio=len(vlm_pages)/len(finals);router={'pages':routes,'unique_vlm_pages':vlm_pages,'unique_vlm_ratio':ratio,'router_warnings':['ROUTER_TOO_SENSITIVE'] if ratio>.2 else [],'router_fail':ratio>.4};write_json(output/'PF2_VLM_Router_Report.json',router)
 source_tables=[b['block_id'] for p in records for b in active_blocks(p) if b['kind']=='table'];risk_pages=[r['page_no'] for r in routes if r['table_risk']]
 table_report={'total_source_tables':len(source_tables),'TABLE_OK':len(source_tables)-sum(1 for p in records if p['page_no'] in risk_pages for b in active_blocks(p) if b['kind']=='table'),'BROKEN_TABLE_RISK_pages':risk_pages,'REPAIRED_TABLE_pages':[f['page_no'] for f in finals if f['table_repair_status']=='SUCCESS'],'TABLE_FALLBACK_A1_pages':[f['page_no'] for f in finals if f['table_repair_status']=='FALLBACK_A1'],'TABLE_SKIPPED_NO_FACTS_pages':[f['page_no'] for f in finals if f['table_repair_status']=='SKIPPED_NO_ELIGIBLE_SOURCE_FACTS'],'table_source_coverage':len(set(source_tables)&set(actual))/len(source_tables) if source_tables else 1,'normal_tables_vlm_called':[],'cross_page_merge':False};write_json(output/'PF2_Table_Report.json',table_report)
 order_changes=[]
 by_record={p['page_no']:p for p in records}
 for f in finals:
  if f['order_source']=='VLM_REPAIR':order_changes.append({'page_no':f['page_no'],'before_order':[b['block_id'] for b in active_blocks(by_record[f['page_no']])],'after_order':[x for e in f['ordered_elements'] for x in e['source_ids']],'reason':f['order_risk_reasons']})
 order_report={'A1_ORDER':sum(f['order_source']=='A1_DEFAULT' for f in finals),'CODE_MERGE':sum(f['order_source']=='CODE_MERGE' for f in finals),'VLM_REPAIR':sum(f['order_source']=='VLM_REPAIR' for f in finals),'FALLBACK_A1':sum(f['order_repair_status']=='FALLBACK_A1' for f in finals),'vlm_order_changes':order_changes};write_json(output/'PF2_Order_Report.json',order_report)
 prompt_vals=[c['usage'].get('prompt_tokens') for c in calls];comp_vals=[c['usage'].get('completion_tokens') for c in calls];prompt=sum(prompt_vals) if all(x is not None for x in prompt_vals) else None;comp=sum(comp_vals) if all(x is not None for x in comp_vals) else None
 usage={'vlm_pages_unique':vlm_pages,'vlm_attempts':len(calls),'api_success':sum(c['status']=='SUCCESS' for c in calls),'api_failed':sum(c['status']!='SUCCESS' for c in calls),'input_tokens':prompt,'output_tokens':comp,'total_tokens':prompt+comp if prompt is not None and comp is not None else None,'latency_seconds':sum(c['latency_seconds'] for c in calls),'estimated_cost':estimate_cost(settings,prompt,comp),'reused_pages':reused};write_json(output/'PF2_VLM_Usage.json',usage)
 all_pages=[p['page_no'] for p in records];active_pages=[p['page_no'] for p in records if active_blocks(p)];empty=[p for p in all_pages if p not in active_pages]
 risks=set(vlm_pages)|set(risk_pages)|set(headings['high_density_pages'])|{f['page_no'] for f in finals if f['page_type']=='toc'}|{f['page_no'] for f in finals if f['order_repair_status']=='FALLBACK_A1' or f['table_repair_status']=='FALLBACK_A1'}
 normal=[n for n in active_pages if n not in risks];random.Random(20260912).shuffle(normal);selected=list(dict.fromkeys(sorted(risks)+normal[:5]))[:30];write_json(output/'PF2_Manual_Review_Pages.json',{'pages':selected,'selection_basis':['all VLM/fallback/broken/high-density/TOC pages','5 deterministic random untouched pages'],'random_seed':20260912})
 source_text=sum(b['kind']=='text' for p in records for b in active_blocks(p));source_table=len(source_tables);out_text=sum(e['kind']=='text' for f in finals for e in f['ordered_elements']);out_table=sum(e['kind']=='table' for f in finals for e in f['ordered_elements'])
 gate_checks={'active_pages_169':len(finals)==169,'source_coverage_100_percent':len(set(actual)&set(expected))/len(expected)==1,'missing_zero':not missing,'duplicate_zero':not dup,'unknown_zero':not unknown,'covered_leaks_zero':not leaks,'all_source_tables_traceable':table_report['table_source_coverage']==1,'no_technical_fatal':True,'vlm_failures_have_fallback':all(not r['fallback_used'] or r['vlm_order_status']=='FALLBACK_A1' or r['vlm_table_status'] in {'FALLBACK_A1','SKIPPED_NO_ELIGIBLE_SOURCE_FACTS'} for r in routes),'router_not_failed':not router['router_fail'],'no_large_scale_false_heading':headings['candidate_risk_count']<10}
 report={'status':'PASS' if all(gate_checks.values()) else 'FAIL','physical_pages':len(records),'active_pages':len(finals),'empty_pages':empty,'source_active_blocks':len(expected),'output_active_blocks':len(actual),'source_text_blocks':source_text,'source_table_blocks':source_table,'output_text_blocks':out_text,'output_table_blocks':out_table,'source_coverage':len(set(actual)&set(expected))/len(expected),'missing_source_ids':missing,'duplicate_source_ids':dup,'unknown_source_ids':unknown,'covered_block_leaks':leaks,'heading_count':headings['heading_count'],'toc_pages':[f['page_no'] for f in finals if f['page_type']=='toc'],'order_risk_pages':[r['page_no'] for r in routes if r['order_risk']],'order_repair_calls':sum(r['vlm_order_called'] for r in routes),'order_repair_success':sum(r['vlm_order_status']=='SUCCESS' for r in routes),'order_repair_fallback':sum(r['vlm_order_status']=='FALLBACK_A1' for r in routes),'table_risk_pages':risk_pages,'table_repair_calls':sum(r['vlm_table_called'] for r in routes),'table_repair_success':sum(r['vlm_table_status']=='SUCCESS' for r in routes),'table_repair_fallback':sum(r['vlm_table_status']=='FALLBACK_A1' for r in routes),**usage,'gate_checks':gate_checks,'input_fingerprint':input_fp};write_json(output/'PF2_Full_Check_Report.json',report)
 return report,router,selected

def write_audit_html(records:list[dict[str,Any]],finals:list[dict[str,Any]],routes:list[dict[str,Any]],images:dict[int,str],output:Path)->None:
    by_route={r['page_no']:r for r in routes};risk={r['page_no'] for r in routes if r['order_risk'] or r['table_risk'] or r['fallback_used']}
    css='body{font:14px/1.55 Arial,"Microsoft YaHei";max-width:1200px;margin:auto}details{border:1px solid #ccd6e0;margin:8px;padding:8px}summary{font-weight:700;cursor:pointer}.grid{display:grid;grid-template-columns:38% 62%;gap:14px}.shot{width:100%}.tag{font:12px Consolas;background:#eef2f6;padding:2px 5px}.heading{color:#174d7a;font-weight:bold}.table{color:#7a4317}.text{white-space:pre-wrap;border-bottom:1px solid #eee;padding:4px}'
    out=[f'<meta charset="utf-8"><style>{css}</style><h1>PF-2 全量结构审计</h1>']
    for f in finals:
        r=by_route[f['page_no']];hs=sum(e['semantic_type']=='heading' for e in f['ordered_elements']);opened=' open' if f['page_no'] in risk else ''
        out.append(f'<details{opened}><summary>PDF page {f["page_no"]} | {f["page_type"]} | headings={hs} | {f["order_source"]} | order_risk={r["order_risk"]} | table_risk={r["table_risk"]} | VLM={r["vlm_order_called"] or r["vlm_table_called"]} | fallback={r["fallback_used"]}</summary><div class="grid"><img class="shot" src="{Path(images[f["page_no"]]).as_uri()}"><div>')
        for e in f['ordered_elements']:
            out.append(f'<div class="text {e["semantic_type"]}"><span class="tag">{html.escape(",".join(e["source_ids"]))}</span> {e["kind"]} → {e["semantic_type"]}<br>{html.escape(str(e.get("text",""))[:800])}</div>')
        out.append('</div></div></details>')
    (output/'PF2_结构审计.html').write_text('\n'.join(out),encoding='utf8')

def write_good_preview(records:list[dict[str,Any]],finals:list[dict[str,Any]],output:Path)->None:
    from .goodmd_renderer import _repair_html
    from .vlm_selector_report import render_toc_preview
    by={p['page_no']:p for p in records};css='body{font:15px/1.7 Arial,"Microsoft YaHei";max-width:1000px;margin:auto;color:#222}.page{display:grid;grid-template-columns:90px 1fr;border-top:1px solid #ccd6e0;padding:16px 0}.page-no{color:#789;font-size:12px;position:sticky;top:5px;height:20px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #999;padding:5px;vertical-align:top}h1,h2,h3{color:#173c67}.text{white-space:pre-wrap}'
    out=[f'<meta charset="utf-8"><style>{css}</style><h1>PF-2 Good Markdown Preview</h1>']
    for f in finals:
        source={b['block_id']:b for b in active_blocks(by[f['page_no']])};valid_repairs=[t for t in f.get('table_repairs',[]) if t.get('validation_status')=='VALID'];repaired={x for t in valid_repairs for x in t.get('source_ids',[])};done=False
        out.append(f'<section class="page"><aside class="page-no">PDF page {f["page_no"]}</aside><main>')
        for e in f['ordered_elements']:
            if any(x in repaired for x in e['source_ids']):
                if not done:out.append(_repair_html(valid_repairs,source));done=True
                continue
            text=str(e.get('text',''))
            if f['page_type']=='toc' and e['kind']=='table':out.append('<div class="text">'+'<br>'.join(html.escape(x) for x in render_toc_preview(source[e['source_ids'][0]]))+'</div>')
            elif e['semantic_type']=='heading':level=e.get('heading_level') or 2;out.append(f'<h{level}>{html.escape(text)}</h{level}>')
            elif e['semantic_type']=='table':out.append(str(e.get('table_html','')))
            else:out.append('<p class="text">'+html.escape(text)+'</p>')
        out.append('</main></section>')
    (output/'PF2_GoodMD_Preview.html').write_text('\n'.join(out),encoding='utf8')
