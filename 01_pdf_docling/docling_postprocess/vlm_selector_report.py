"""A2.2 statistics, comparison, risk audit, and deterministic previews."""

from __future__ import annotations

import html
import json
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .vlm_selector_prompt import SCHEMA_VERSION, active_blocks
from .vlm_selector_schema import validate_selector
from .vlm_structure import read_jsonl, write_json

DEEP_NUMBER=re.compile(r"^\s*\d+\.\d+\.\d+")


class _TableRows(HTMLParser):
    def __init__(self): super().__init__(); self.rows=[]; self.row=None; self.cell=None
    def handle_starttag(self,tag,attrs):
        if tag.lower()=='tr': self.row=[]
        elif tag.lower() in {'td','th'} and self.row is not None: self.cell=[]
    def handle_data(self,data):
        if self.cell is not None: self.cell.append(data)
    def handle_endtag(self,tag):
        if tag.lower() in {'td','th'} and self.cell is not None:
            value=' '.join(''.join(self.cell).split())
            if value: self.row.append(value)
            self.cell=None
        elif tag.lower()=='tr' and self.row is not None:
            if self.row: self.rows.append(self.row)
            self.row=None


def render_toc_preview(block:dict[str,Any])->list[str]:
    """Deterministically join A1 table cells by row; no OCR or model call."""
    source=str(block.get('table_html',''))
    if source:
        parser=_TableRows(); parser.feed(source)
        if parser.rows: return [' '.join(row) for row in parser.rows]
    text=str(block.get('text',''))
    return [' '.join(part.strip() for part in line.split('|') if part.strip())
            for line in text.splitlines() if line.strip()]


def suspect_boundaries(page:dict[str,Any],row:dict[str,Any])->list[dict[str,Any]]:
    by={b['block_id']:b for b in active_blocks(page)}; result=[]
    for e in row.get('elements',[]):
        if e.get('chunk_boundary') is not True: continue
        b=by.get(e.get('block_id'),{}); text=' '.join(str(b.get('text','')).split()); reasons=[]
        if '：' in text and len(text.split('：',1)[1].strip())>=2: reasons.append('colon_with_specific_content')
        if len(text)>80: reasons.append('text_longer_than_80')
        if text.rstrip().endswith(('；','。',';','.')): reasons.append('prose_ending_punctuation')
        if DEEP_NUMBER.match(text): reasons.append('deep_numbered_clause')
        if len(text)>35 and not re.match(r'^\s*(第\s*\d+\s*[章节]|[（(][一二三四五六七八九十]+[）)])',text):
            reasons.append('likely_complete_clause_or_continuation')
        if reasons: result.append({'page_no':row['page_no'],'block_id':e['block_id'],
            'text_preview':text[:240],'chunk_boundary':True,'risk_reason':reasons})
    return result


def _order_a1(page): return [b['block_id'] for b in active_blocks(page)]
def _order_old(row): return row.get('reading_order',[])
def _order_elements(row): return [e['block_id'] for e in row.get('elements',[])]


def build_comparison(page_records,rows,old_full_rows,a21_rows):
    pages={p['page_no']:p for p in page_records}; old={r['page_no']:r for r in old_full_rows}; a21={r['page_no']:r for r in a21_rows}
    result=[]
    for row in rows:
        p=row['page_no']; a21_row=a21.get(p,{}); a21_hard=sum(e.get('boundary_role')=='hard' for e in a21_row.get('elements',[]))
        boundary=sum(e.get('chunk_boundary') is True for e in row.get('elements',[]))
        result.append({'page_no':p,'a1_initial_order':_order_a1(pages[p]),'a2_full_order':_order_old(old.get(p,{})),
          'a2_1_order':_order_elements(a21_row),'a2_2_order':_order_elements(row),
          'a2_1_hard_count':a21_hard,'a2_2_chunk_boundary_count':boundary,
          'a2_2_matches_a2_full_order':bool(old.get(p,{}).get('status')=='SUCCESS' and _order_old(old[p])==_order_elements(row)),
          'a2_2_matches_a1_order':_order_a1(pages[p])==_order_elements(row)})
    by={x['page_no']:x for x in result}
    return {'schema_version':SCHEMA_VERSION,'pages_compared':len(result),'pages':result,
      'focus':{str(p):by[p] for p in (5,35,38,91,99,127)},
      'summary':{'page_5_hard_to_boundary':[by[5]['a2_1_hard_count'],by[5]['a2_2_chunk_boundary_count']],
       'page_38_hard_to_boundary':[by[38]['a2_1_hard_count'],by[38]['a2_2_chunk_boundary_count']],
       'page_99_hard_to_boundary':[by[99]['a2_1_hard_count'],by[99]['a2_2_chunk_boundary_count']],
       'reading_order_improvement_preserved_pages':[p for p in (35,127) if by[p]['a2_2_matches_a2_full_order']]}}


def _page_markup(page,row,simulation=False):
    by={b['block_id']:b for b in active_blocks(page)}; parts=[]
    toc=row.get('page_type')=='toc'
    if toc: parts.append('<h2>目录</h2>')
    for e in row.get('elements',[]):
        b=by[e['block_id']]
        if b['kind']=='table':
            if toc:
                parts.extend(f"<div class='tocline'>{html.escape(line)}</div>" for line in render_toc_preview(b))
            else: parts.append(str(b.get('table_html') or f"<pre>{html.escape(str(b.get('text','')))}</pre>"))
        else:
            text=html.escape(str(b.get('text','')))
            parts.append(f"<h2>{text}</h2>" if e['chunk_boundary'] else f"<p>{text}</p>")
    return ''.join(parts)


def write_previews(output_dir,page_records,rows,a1_dir):
    pages={p['page_no']:p for p in page_records}; audit=[]; simulation=[]; md=[]
    for row in rows:
        p=row['page_no']; by={b['block_id']:b for b in active_blocks(pages[p])}; trs=[]
        for i,e in enumerate(row.get('elements',[]),1):
            b=by[e['block_id']]; preview=str(b.get('text',''))
            trs.append(f"<tr class='b{str(e['chunk_boundary']).lower()}'><td>{i}</td><td><code>{e['block_id']}</code></td><td>{b['kind']}</td><td>{e['chunk_boundary']}</td><td>{html.escape(preview)}</td></tr>")
        image=(a1_dir/'page_images'/f'page_{p:04d}.jpg').as_uri()
        audit.append(f"<section><h2>PDF {p} · {row['page_type']} · {row['status']}</h2><div class='grid'><img src='{image}'><table><tr><th>#</th><th>block</th><th>kind</th><th>boundary</th><th>原文</th></tr>{''.join(trs)}</table></div></section>")
        simulation.append(f"<section><div class='page'>PDF physical page {p}</div>{_page_markup(pages[p],row,True)}</section>")
        md.extend([f"\n---\n\n*PDF physical page {p}*\n"])
        if row['page_type']=='toc': md.append('\n## 目录\n')
        for e in row.get('elements',[]):
            b=by[e['block_id']]
            if b['kind']=='table':
                if row['page_type']=='toc': md.extend('\n'+line+'\n' for line in render_toc_preview(b))
                else: md.append('\n'+str(b.get('table_html') or b.get('text',''))+'\n')
            else: md.append(('\n## ' if e['chunk_boundary'] else '\n')+str(b.get('text',''))+'\n')
    css="body{font-family:system-ui;margin:24px;color:#17202a}section{margin:26px auto;max-width:1200px}.page{color:#789;border-top:1px solid #ccd;padding-top:8px}p{white-space:pre-wrap;line-height:1.7}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccd;padding:6px;vertical-align:top}.grid{display:grid;grid-template-columns:42% 1fr;gap:16px}img{width:100%}.btrue{background:#ffe5e2}.tocline{padding:5px 0;border-bottom:1px dotted #bbb}"
    (output_dir/'A2_2_结构审计.html').write_text("<!doctype html><meta charset='utf-8'><style>"+css+"</style><h1>A2.2 结构审计</h1>"+''.join(audit),encoding='utf-8')
    (output_dir/'A2_2_模拟GoodMD.html').write_text("<!doctype html><meta charset='utf-8'><style>"+css+"</style><h1>A2.2 模拟 Good Markdown</h1>"+''.join(simulation),encoding='utf-8')
    (output_dir/'A2_2_模拟GoodMD.md').write_text(''.join(md).lstrip(),encoding='utf-8')


def generate_selector_reports(page_records,rows,selected,output_dir,a1_dir,old_full_path,a21_path,settings):
    pages={p['page_no']:p for p in page_records}; statuses=Counter(r['status'] for r in rows); boundaries=0; text_count=0; suspects=[]; density=[]
    for row in rows:
        page=pages[row['page_no']]; by={b['block_id']:b for b in active_blocks(page)}
        text_ids={k for k,b in by.items() if b['kind']=='text'}; trues=sum(e['chunk_boundary'] is True for e in row.get('elements',[]) if e['block_id'] in text_ids)
        boundaries+=trues;text_count+=len(text_ids)
        if row['status']=='SUCCESS': suspects.extend(suspect_boundaries(page,row))
        ratio=trues/len(text_ids) if text_ids else 0
        if row.get('page_type')=='content' and len(text_ids)>=6 and ratio>.35:
            density.append({'page_no':row['page_no'],'active_text_blocks':len(text_ids),'boundary_true':trues,'ratio':ratio,
                            'risk':'HIGH_BOUNDARY_DENSITY' if ratio>.5 else 'SUSPECT_BOUNDARY_DENSITY'})
    stats={'schema_version':SCHEMA_VERSION,'pages_requested':len(selected),'success':statuses['SUCCESS'],'invalid':statuses['INVALID'],
      'invalid_truncated':statuses['INVALID_TRUNCATED'],'technical_failed':statuses['TECHNICAL_FAILED'],
      'total_active_blocks':sum(len(active_blocks(pages[p])) for p in selected),'active_text_blocks':text_count,
      'chunk_boundary_true':boundaries,'chunk_boundary_false':sum(len(r.get('elements',[])) for r in rows)-boundaries,
      'page_type_counts':dict(Counter(r.get('page_type') for r in rows)),'finish_reason_counts':dict(Counter(r.get('finish_reason') for r in rows if r.get('finish_reason'))),
      'suspect_chunk_boundary_count':len(suspects),'boundary_density_risk_pages':len(density)}
    comparison=build_comparison(page_records,rows,read_jsonl(old_full_path),read_jsonl(a21_path))
    validation_errors={}; table_wrong=[]; covered_returned=[]
    for row in rows:
        page=pages[row['page_no']]; active=active_blocks(page)
        covered={b['block_id'] for b in page.get('blocks',[]) if 'COVERED_BY_TABLE' in b.get('flags',[])}
        value={k:row.get(k) for k in ('schema_version','page_no','page_type','elements')}
        if row['status']=='SUCCESS':
            found=validate_selector(value,row['page_no'],active,covered,row.get('finish_reason'))
            if found: validation_errors[str(row['page_no'])]=found
        ids={e.get('block_id') for e in row.get('elements',[])};covered_returned.extend(sorted(ids&covered))
        by={b['block_id']:b for b in active}
        table_wrong.extend(e['block_id'] for e in row.get('elements',[]) if by.get(e.get('block_id'),{}).get('kind')=='table' and e.get('chunk_boundary') is not False)
    output_files=[p for p in output_dir.rglob('*') if p.is_file()]
    leaked=any(settings.api_key and settings.api_key in p.read_text(encoding='utf-8',errors='ignore') for p in output_files)
    checks={'pages_called_15':len([r for r in rows if r.get('api_called')])==15,'success_15':statuses['SUCCESS']==15,
      'invalid_zero':statuses['INVALID']==0,'invalid_truncated_zero':statuses['INVALID_TRUNCATED']==0,
      'technical_failed_zero':statuses['TECHNICAL_FAILED']==0,'all_success_strictly_valid':not validation_errors,
      'all_active_blocks_preserved':all(len(r.get('elements',[]))==len(active_blocks(pages[r['page_no']])) for r in rows),
      'table_boundary_false':not table_wrong,'toc_boundary_false':all(not e['chunk_boundary'] for r in rows if r.get('page_type')=='toc' for e in r.get('elements',[])),
      'covered_by_table_not_returned':not covered_returned,'api_key_not_leaked':not leaked,
      'raw_and_audit_complete':all((output_dir/'raw_responses'/f'page_{p:04d}.json').is_file() and (output_dir/'request_audit'/f'page_{p:04d}.json').is_file() for p in selected),
      'no_downstream_outputs':not any((output_dir/n).exists() for n in ('good.md','source_map.jsonl','chunks.jsonl','predictions.jsonl'))}
    check={'phase':'A2.2','status':'PASS' if all(checks.values()) else 'COMPLETE_WITH_ISSUES',
      'technical_gate':'PASS' if all(checks.values()) else 'FAIL','checks':checks,
      'status_counts':dict(statuses),'validation_errors':validation_errors,'table_boundary_wrong':table_wrong,
      'covered_ids_returned':covered_returned,'parser_vlm_config':settings.public_dict()}
    write_json(output_dir/'A2_2结构统计.json',stats);write_json(output_dir/'A2_2_vs_previous.json',comparison)
    write_json(output_dir/'SUSPECT_CHUNK_BOUNDARY.json',{'count':len(suspects),'candidates':suspects})
    write_json(output_dir/'Boundary_Density风险报告.json',{'risk_page_count':len(density),'pages':density})
    write_json(output_dir/'a2_2_check_report.json',check)
    write_previews(output_dir,page_records,rows,a1_dir)
    return {'statistics':stats,'comparison':comparison,'suspects':suspects,'density':density,'check':check}
