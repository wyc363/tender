"""Risk-gated VLM repair router for the Final Parser MVP."""
from __future__ import annotations
import hashlib,json,time
from collections import Counter
from pathlib import Path
from typing import Any,Callable
from .deterministic_structure import active_blocks,build_final_page,source_text_coverage
from .structure_risk import detect_order_risk,detect_table_risk
from .vlm_client import VLMClientResult,call_page_structure
from .vlm_config import ParserVLMSettings,estimate_cost
from .vlm_repair_prompt import ORDER_SYSTEM_PROMPT,TABLE_SYSTEM_PROMPT,build_order_prompt,build_table_prompt,project,table_candidates
from .vlm_repair_schema import order_schema,table_schema,parse,validate_order,validate_table
from .vlm_structure import write_json,write_jsonl
from .table_repair_scope import eligible_blocks,validate_table_repair_scope

PF1_PAGES=[4,5,12,17,35,38,62,74,83,91,99,127,137,140,167]

def _empty_usage(): return {'prompt_tokens':None,'input_tokens':None,'completion_tokens':None,'output_tokens':None,'total_tokens':None}

def _invoke(kind:str,page:dict[str,Any],image:Path,settings:ParserVLMSettings,output:Path,
            call_fn:Callable[...,VLMClientResult],table_scope:dict[str,Any]|None=None)->dict[str,Any]:
    active=active_blocks(page); candidates=active if kind=='order' else (eligible_blocks(page,table_scope) if table_scope else table_candidates(page));ids=[b['block_id'] for b in candidates]
    groups=[{'group_id':g['group_id'],'table_source_ids':g['table_source_ids'],'eligible_source_ids':g['eligible_source_ids']} for g in (table_scope or {}).get('table_groups',[])]
    prompt=build_order_prompt(page) if kind=='order' else build_table_prompt(page,candidates,groups)
    system=ORDER_SYSTEM_PROMPT if kind=='order' else TABLE_SYSTEM_PROMPT
    schema=order_schema(ids) if kind=='order' else table_schema(ids)
    raw_path=output/'raw_responses'/f"page_{page['page_no']:04d}_{kind}.json"
    if raw_path.is_file():
        cached=json.loads(raw_path.read_text(encoding='utf8'));value,errors=parse(str(cached.get('content','')))
        safety=[]
        if value is not None:
            errors += validate_order(value,int(page['page_no']),ids) if kind=='order' else validate_table(value,int(page['page_no']),ids)
            if kind=='table' and table_scope:
                scope_errors,safety=validate_table_repair_scope(value,page,table_scope);errors+=scope_errors
        if not errors:
            raw_response=cached.get('response') or {};raw_usage=raw_response.get('usage') or {}
            prompt_tokens=raw_usage.get('prompt_tokens');completion_tokens=raw_usage.get('completion_tokens')
            usage={'prompt_tokens':prompt_tokens,'input_tokens':prompt_tokens,'completion_tokens':completion_tokens,'output_tokens':completion_tokens,'total_tokens':raw_usage.get('total_tokens')}
            return {'kind':kind,'status':'SUCCESS','api_called':True,'reused_from_cache':True,'value':value,'validation_errors':[],
                    'usage':usage,'latency_seconds':0.0,'structured_output_mode':cached.get('structured_output_mode'),'compatibility_fallbacks':cached.get('compatibility_fallbacks',[]),'scope_validation':safety}
    audit={'phase':'PF-1','repair_kind':kind,'page_no':page['page_no'],'model':settings.model,
           'api_base_host_region':settings.api_region,'active_block_ids':ids,'blocks_sent':[project(b) for b in candidates],
           'table_scope':groups if kind=='table' else None,
           'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest().upper(),'prompt_characters':len(prompt),
           'image_path':str(image),'api_key_in_audit':False}
    write_json(output/'request_audit'/f"page_{page['page_no']:04d}_{kind}.json",audit)
    started=time.perf_counter()
    try:
        response=call_fn(settings,image,prompt,system_prompt=system,response_schema=schema,schema_name=f'pf1_{kind}_repair')
        value,errors=parse(response.text)
        safety=[]
        if value is not None:
            errors += validate_order(value,int(page['page_no']),ids) if kind=='order' else validate_table(value,int(page['page_no']),ids)
            if kind=='table' and table_scope:
                scope_errors,safety=validate_table_repair_scope(value,page,table_scope);errors+=scope_errors
        status='SUCCESS' if not errors else 'INVALID'
        write_json(output/'raw_responses'/f"page_{page['page_no']:04d}_{kind}.json",{'page_no':page['page_no'],'repair_kind':kind,'content':response.text,'response':response.raw_response,'structured_output_mode':response.structured_output_mode,'compatibility_fallbacks':response.compatibility_fallbacks,'finish_reason':response.finish_reason})
        return {'kind':kind,'status':status,'api_called':True,'reused_from_cache':False,'value':value if status=='SUCCESS' else None,
                'validation_errors':errors,'usage':response.usage,'latency_seconds':response.latency_seconds,
                'structured_output_mode':response.structured_output_mode,'compatibility_fallbacks':response.compatibility_fallbacks,'scope_validation':safety}
    except Exception as exc:
        write_json(output/'raw_responses'/f"page_{page['page_no']:04d}_{kind}.json",{'page_no':page['page_no'],'repair_kind':kind,'error_type':type(exc).__name__,'error':str(exc)})
        return {'kind':kind,'status':'TECHNICAL_FAILED','api_called':True,'reused_from_cache':False,'value':None,
                'validation_errors':[f'API_ERROR: {type(exc).__name__}: {exc}'],'usage':_empty_usage(),
                'latency_seconds':time.perf_counter()-started,'structured_output_mode':None,'compatibility_fallbacks':[]}

def run_pf1(page_records:list[dict[str,Any]],manifest:dict[str,Any],output:Path,settings:ParserVLMSettings,
            pages:list[int]=PF1_PAGES,run_api:bool=True,call_fn:Callable[...,VLMClientResult]=call_page_structure):
    output.mkdir(parents=True,exist_ok=True);(output/'raw_responses').mkdir(exist_ok=True);(output/'request_audit').mkdir(exist_ok=True)
    by_page={int(p['page_no']):p for p in page_records}; images={int(x['page_no']):Path(x['path']) for x in manifest['images']}
    finals=[];routes=[];calls=[];coverage=[]
    for n in pages:
        page=by_page[n]; order_risk=detect_order_risk(page);table_risk=detect_table_risk(page)
        order_result=table_result=None
        if run_api and order_risk['needs_vlm']: order_result=_invoke('order',page,images[n],settings,output,call_fn);calls.append(order_result)
        if run_api and table_risk['needs_vlm']: table_result=_invoke('table',page,images[n],settings,output,call_fn);calls.append(table_result)
        repaired_order=(order_result or {}).get('value',{}).get('ordered_block_ids') if (order_result or {}).get('status')=='SUCCESS' else None
        table_repairs=(table_result or {}).get('value',{}).get('tables') if (table_result or {}).get('status')=='SUCCESS' else None
        final=build_final_page(page,repaired_order,table_repairs)
        final['order_risk']=order_risk['risk'];final['table_risk']=table_risk['risk']
        final['order_repair_status']=(order_result or {}).get('status','NOT_CALLED')
        final['table_repair_status']=(table_result or {}).get('status','NOT_CALLED')
        finals.append(final); coverage.append(source_text_coverage(page,final))
        routes.append({'page_no':n,'order_risk':order_risk['risk'],'order_risk_reasons':order_risk['reasons'],
                       'table_risk':table_risk['risk'],'table_risk_reasons':table_risk['reasons'],
                       'vlm_order_called':order_result is not None,'vlm_table_called':table_result is not None,
                       'order_result_status':(order_result or {}).get('status'),'table_result_status':(table_result or {}).get('status'),
                       'fallback_to_a1_order':order_result is not None and (order_result or {}).get('status')!='SUCCESS'})
    write_jsonl(output/'final_page_structures.jsonl',finals);write_json(output/'PF1_VLM_Router_Report.json',{'pages':routes})
    prompt_vals=[c['usage'].get('prompt_tokens') for c in calls];comp_vals=[c['usage'].get('completion_tokens') for c in calls]
    prompt=sum(prompt_vals) if all(x is not None for x in prompt_vals) else None;comp=sum(comp_vals) if all(x is not None for x in comp_vals) else None
    usage={'api_calls':len(calls),'order_repair_calls':sum(c['kind']=='order' for c in calls),'table_repair_calls':sum(c['kind']=='table' for c in calls),
           'api_calls_this_run':sum(not c.get('reused_from_cache') for c in calls),'reused_successful_calls':sum(bool(c.get('reused_from_cache')) for c in calls),
           'successful_calls':sum(c['status']=='SUCCESS' for c in calls),'invalid_calls':sum(c['status']=='INVALID' for c in calls),'technical_failed':sum(c['status']=='TECHNICAL_FAILED' for c in calls),
           'input_tokens':prompt,'output_tokens':comp,'total_tokens':prompt+comp if prompt is not None and comp is not None else None,
           'total_latency_seconds':sum(c['latency_seconds'] for c in calls),'cost':estimate_cost(settings,prompt,comp),
           'structured_output_modes':dict(Counter(c['structured_output_mode'] for c in calls if c['structured_output_mode'])),
           'compatibility_fallbacks':list(dict.fromkeys(x for c in calls for x in c['compatibility_fallbacks']))}
    stats={'pages':len(pages),'high_confidence_heading_count':sum(e['semantic_type']=='heading' for f in finals for e in f['ordered_elements']),
           'order_risk_pages':[r['page_no'] for r in routes if r['order_risk']], 'table_risk_pages':[r['page_no'] for r in routes if r['table_risk']],
           'untouched_pages':[r['page_no'] for r in routes if not r['vlm_order_called'] and not r['vlm_table_called']],
           'source_text_coverage':coverage,'source_text_coverage_100_percent':all(c['coverage']==1 and not c['missing'] and not c['duplicate'] and not c['unknown'] for c in coverage),
           'table_source_coverage_100_percent':all(not c['missing'] for c in coverage)}
    write_json(output/'PF1_VLM_Usage.json',usage);write_json(output/'PF1_Statistics.json',stats)
    return finals,routes,usage,stats
