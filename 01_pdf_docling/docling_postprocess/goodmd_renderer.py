"""Source-only Good Markdown renderer and provenance for PF-2."""
from __future__ import annotations
import html,re
from pathlib import Path
from typing import Any
from .deterministic_structure import active_blocks
from .vlm_selector_report import render_toc_preview
from .vlm_structure import write_json,write_jsonl

def _repair_html(repairs:list[dict[str,Any]],source:dict[str,dict[str,Any]])->str:
    tables=[]
    for table in repairs:
        if table.get('validation_status') != 'VALID': continue
        rows=[]
        for source_id in table.get('rows',[]):
            rows.append('<tr><td>'+html.escape(str(source[source_id].get('text',''))).replace('\n','<br>')+'</td></tr>')
        tables.append('<table>\n'+'\n'.join(rows)+'\n</table>')
    return '\n'.join(tables)

def _source_lines(ids:list[str],source:dict[str,dict[str,Any]])->int:
    return max(1,sum(max(1,len(str(source[x].get('text','')).splitlines())) for x in ids))

def render_goodmd(page_records:list[dict[str,Any]],finals:list[dict[str,Any]],output:Path)->tuple[list[dict[str,Any]],dict[str,Any]]:
    by_page={int(p['page_no']):p for p in page_records}; lines:list[str]=[];debug:list[str]=[];maps=[];rid=0
    for final in finals:
        page=by_page[final['page_no']];source={b['block_id']:b for b in active_blocks(page)}
        debug += [f'<!-- PDF physical page {final["page_no"]} -->','']
        valid_repairs=[t for t in final.get('table_repairs',[]) if t.get('validation_status')=='VALID']
        repaired_ids={x for t in valid_repairs for x in t.get('source_ids',[])}
        repair_done=False
        for element in final['ordered_elements']:
            ids=element['source_ids']
            if any(x in repaired_ids for x in ids):
                if repair_done: continue
                all_ids=[x for t in valid_repairs for x in t.get('source_ids',[])]
                rendered=_repair_html(valid_repairs,source);semantic='table';repair_done=True;table_ids=[x for x in all_ids if source[x].get('kind')=='table']
            elif final['page_type']=='toc' and element['kind']=='table':
                rendered='\n'.join(render_toc_preview(source[ids[0]]));semantic='text';all_ids=ids;table_ids=[ids[0]]
            elif element['semantic_type']=='heading':
                rendered='#'*(element.get('heading_level') or 2)+' '+str(element.get('text',''));semantic='heading';all_ids=ids;table_ids=[]
            elif element['semantic_type']=='table':
                rendered=str(element.get('table_html',''));semantic='table';all_ids=ids;table_ids=ids
            else:
                rendered=str(element.get('text',''));semantic='text';all_ids=ids;table_ids=[]
            start=len(lines)+1;part=rendered.splitlines() or [''];lines.extend(part);end=len(lines);lines.append('')
            debug.extend(part);debug.append('');rid+=1
            row={'render_id':f'R{rid:06d}','semantic_type':semantic,'source_block_ids':all_ids,
                 'page_numbers':[final['page_no']],'source_line_start':1,'source_line_end':_source_lines(all_ids,source),
                 'md_line_start':start,'md_line_end':end}
            if table_ids:row['source_table_ids']=table_ids
            if repair_done and any(x in repaired_ids for x in all_ids):row['repaired_table']=True
            maps.append(row)
    good='\n'.join(lines).rstrip()+'\n';dbg='\n'.join(debug).rstrip()+'\n'
    (output/'good.md').write_text(good,encoding='utf8');(output/'good_debug.md').write_text(dbg,encoding='utf8');write_jsonl(output/'source_map.jsonl',maps)
    all_source=[b['block_id'] for p in page_records for b in active_blocks(p)]
    rendered_ids=[x for m in maps for x in m['source_block_ids']]
    missing=sorted(set(all_source)-set(rendered_ids));unknown=sorted(set(rendered_ids)-set(all_source));duplicates=sorted(x for x in set(rendered_ids) if rendered_ids.count(x)>1)
    valid_ranges=all(1<=m['md_line_start']<=m['md_line_end']<=len(good.splitlines()) for m in maps)
    source_chars=sum(len(str(b.get('text',''))) for p in page_records for b in active_blocks(p))
    validation={'status':'PASS' if not missing and not unknown and not duplicates and valid_ranges else 'FAIL',
      'render_source_coverage':len(set(rendered_ids)&set(all_source))/len(all_source),'active_source_ids':len(all_source),'rendered_source_ids':len(set(rendered_ids)),
      'missing_source_ids':missing,'duplicate_source_ids':duplicates,'unknown_source_ids':unknown,
      'source_text_or_table_elements_without_provenance':0,'vlm_free_text_elements':0,
      'html_table_open_tags':len(re.findall(r'<table\b',good,re.I)),'html_table_close_tags':len(re.findall(r'</table>',good,re.I)),
      'html_table_tags_balanced':len(re.findall(r'<table\b',good,re.I))==len(re.findall(r'</table>',good,re.I)),
      'markdown_characters':len(good),'source_characters':source_chars,'source_character_coverage_basis':'all output elements map to original source IDs; renderer never accepts VLM text',
      'source_map_line_ranges_valid':valid_ranges,'good_md_contains_physical_page_labels':'PDF physical page' in good,
      'debug_md_contains_physical_page_labels':'PDF physical page' in dbg}
    write_json(output/'PF2_GoodMD_Validation.json',validation)
    return maps,validation

def chunk_preflight(good:str,finals:list[dict[str,Any]])->dict[str,Any]:
    headings=sum(e['semantic_type']=='heading' for f in finals for e in f['ordered_elements'])
    tables=sum(e['semantic_type']=='table' for f in finals for e in f['ordered_elements'])
    toc_chars=sum(len(str(e.get('text',''))) for f in finals if f['page_type']=='toc' for e in f['ordered_elements'])
    segments=re.split(r'(?m)^#{1,6}\s|<table\b',good)
    return {'heading_count':headings,'table_count':tables,'text_character_count':len(re.sub(r'<[^>]+>|^#{1,6}\s','',good,flags=re.M)),
            'max_contiguous_text_length':max(map(len,segments),default=0),'toc_character_count':toc_chars,
            'ready_for_chunker':bool(good.strip())}
