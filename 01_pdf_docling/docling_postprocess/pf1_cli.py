"""Bounded Phase PF-1 Final Parser MVP entry point."""
from __future__ import annotations
import argparse,html,json
from pathlib import Path
from typing import Any
from .deterministic_structure import active_blocks
from .vlm_cli import _validate_a1_inputs
from .vlm_config import load_parser_vlm_settings,validate_parser_vlm_settings
from .vlm_repair_router import PF1_PAGES,run_pf1
from .vlm_structure import read_jsonl,write_json

def _toc_lines(block:dict[str,Any])->list[str]:
    from .vlm_selector_report import render_toc_preview
    return render_toc_preview(block)

def _table_repair_html(repair:list[dict[str,Any]],source:dict[str,dict[str,Any]])->str:
    chunks=[]
    for table in repair:
        rows=[]
        for source_id in table.get('rows',[]):
            rows.append('<tr><td>'+html.escape(str(source[source_id].get('text',''))).replace('\n','<br>')+'</td></tr>')
        chunks.append('<table>'+''.join(rows)+'</table>')
    return ''.join(chunks)

def generate_outputs(records:list[dict[str,Any]],finals:list[dict[str,Any]],routes:list[dict[str,Any]],usage:dict[str,Any],stats:dict[str,Any],a1:Path,out:Path)->None:
    by_page={int(x['page_no']):x for x in records}; by_route={x['page_no']:x for x in routes}
    css='''body{font:15px/1.65 Arial,"Microsoft YaHei";max-width:1180px;margin:auto;color:#222}.page{border-top:2px solid #ccd6e0;padding:20px 0}.audit{display:grid;grid-template-columns:42% 58%;gap:20px}.shot{width:100%;border:1px solid #ddd}.tag{font:12px Consolas;background:#eef2f6;padding:3px 6px}.heading{font-weight:700;color:#183a62}.text{white-space:pre-wrap}.physical{color:#789;font-size:12px}table{border-collapse:collapse;width:100%;margin:8px 0}td,th{border:1px solid #aaa;padding:5px;vertical-align:top}.good{max-width:900px}.good h1,.good h2,.good h3{color:#173c67}'''
    audit=[f'<meta charset="utf-8"><style>{css}</style><h1>PF-1 结构审计</h1>']
    good=[f'<meta charset="utf-8"><style>{css}</style><div class="good"><h1>PF-1 模拟 Good Markdown</h1>']
    md=['# PF-1 模拟 Good Markdown','']
    for final in finals:
        n=final['page_no'];page=by_page[n];route=by_route[n];source={b['block_id']:b for b in active_blocks(page)}
        audit += [f'<section class="page"><h2>PDF physical page {n}</h2><div class="audit"><img class="shot" src="{(a1/"page_images"/f"page_{n:04d}.jpg").as_uri()}"><div>',
                  f'<p>order risk={route["order_risk"]} {html.escape(str(route["order_risk_reasons"]))}<br>table risk={route["table_risk"]} {html.escape(str(route["table_risk_reasons"]))}<br>order source={final["order_source"]}</p>']
        good += [f'<section class="page"><div class="physical">PDF physical page {n}</div>'];md += [f'<!-- PDF physical page {n} -->','']
        repair_html=_table_repair_html(final.get('table_repairs',[]),source) if final.get('table_repairs') else ''
        repaired_ids={x for t in final.get('table_repairs',[]) for x in t.get('source_ids',[])}
        for e in final['ordered_elements']:
            audit.append(f'<div><span class="tag">{html.escape(",".join(e["source_ids"]))}</span> <b>{e["semantic_type"]}</b> {html.escape(e.get("decision",""))}<div class="text">{html.escape(str(e.get("text","")))}</div></div>')
            if any(x in repaired_ids for x in e['source_ids']): continue
            text=str(e.get('text',''))
            if final['page_type']=='toc' and e['kind']=='table':
                lines=_toc_lines(source[e['source_ids'][0]]);good.append('<div class="text">'+'<br>'.join(html.escape(x) for x in lines)+'</div>');md += lines+[''];continue
            if e['semantic_type']=='heading':
                level=e.get('heading_level') or 2;good.append(f'<h{level}>{html.escape(text)}</h{level}>');md += ['#'*level+' '+text,'']
            elif e['semantic_type']=='table':
                good.append(str(e.get('table_html','')));md += [str(e.get('table_html','')),'']
            else:good.append('<p class="text">'+html.escape(text)+'</p>');md += [text,'']
        if repair_html:
            good += ['<h3>修复后的逻辑表格</h3>',repair_html]
            md += [repair_html,'']
        audit += ['</div></div></section>'];good += ['</section>']
    audit.append('</body>');good.append('</div>')
    (out/'PF1_结构审计.html').write_text('\n'.join(audit),encoding='utf8')
    (out/'PF1_模拟GoodMD.html').write_text('\n'.join(good),encoding='utf8')
    (out/'PF1_模拟GoodMD.md').write_text('\n'.join(md),encoding='utf8')
    lines=['# PF-1 15页逐页人工验收','',f'- source_text_coverage：{"100%" if stats["source_text_coverage_100_percent"] else "未达标"}',f'- VLM API calls：{usage["api_calls"]}','', '| PDF页 | 结构策略 | Order Risk | Table Risk | 初步检查 |','|---:|---|---|---|---|']
    for f in finals:
        r=by_route[f['page_no']];lines.append(f'| {f["page_no"]} | {f["order_source"]} | {r["order_risk"]} | {r["table_risk"]} | 待结合审计预览确认 |')
    (out/'PF1_15页人工验收.md').write_text('\n'.join(lines)+'\n',encoding='utf8')

def main()->None:
 p=argparse.ArgumentParser();p.add_argument('--pdf',type=Path,required=True);p.add_argument('--json',type=Path,required=True);p.add_argument('--a1-dir',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);p.add_argument('--env-file',type=Path)
 g=p.add_mutually_exclusive_group(required=True);g.add_argument('--run',action='store_true');g.add_argument('--check-only',action='store_true');a=p.parse_args()
 _validate_a1_inputs(a.a1_dir.resolve(),a.pdf.resolve(),a.json.resolve());settings=load_parser_vlm_settings(a.env_file);validate_parser_vlm_settings(settings,require_api=a.run)
 records=read_jsonl(a.a1_dir/'page_blocks.jsonl');manifest=json.loads((a.a1_dir/'page_images'/'manifest.json').read_text(encoding='utf8'))
 finals,routes,usage,stats=run_pf1(records,manifest,a.output_dir.resolve(),settings,PF1_PAGES,a.run)
 generate_outputs(records,finals,routes,usage,stats,a.a1_dir.resolve(),a.output_dir.resolve())
 write_json(a.output_dir/'PF1_Check_Report.json',{'status':'PASS' if stats['source_text_coverage_100_percent'] and not usage['technical_failed'] and not usage['invalid_calls'] else 'COMPLETE_WITH_ISSUES','phase':'PF-1','pages':PF1_PAGES,'api_key':'CONFIGURED' if settings.api_configured else 'MISSING','scope_exclusions':['169 pages','formal Good Markdown','source_map','Chunker','Retrieval','Judge','cross-page table merge']})
 print(json.dumps({'status':'COMPLETE','pages':PF1_PAGES,'output_dir':str(a.output_dir),'api_calls':usage['api_calls'],'input_tokens':usage['input_tokens'],'output_tokens':usage['output_tokens'],'estimated_cost_cny':usage['cost'].get('estimated_cost')},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
