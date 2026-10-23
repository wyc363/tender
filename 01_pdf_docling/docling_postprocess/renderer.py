import html
from collections import Counter
from .loader import display


def text_markdown(item):
    text = display(item)
    if item.get('label') == 'list_item':
        marker = item.get('marker') or ''
        if item.get('enumerated') and marker and not text.lstrip().startswith(marker):
            text = marker+' '+text
        return '- '+text.replace('\n','\n  ')
    if item.get('label') == 'section_header':
        return '## '+text
    return text


def table_html(item):
    data = item.get('data', {})
    cells = data.get('table_cells', [])
    starts = {(c['start_row_offset_idx'],c['start_col_offset_idx']):c for c in cells}
    covered = set()
    emitted_cells = set()
    lines = ['<table>']
    for row in range(data.get('num_rows',0)):
        lines.append('<tr>')
        for col in range(data.get('num_cols',0)):
            if (row,col) in covered: continue
            c = starts.get((row,col))
            if not c:
                lines.append('<td></td>')
                continue
            rs,cs = c.get('row_span',1),c.get('col_span',1)
            emitted_cells.add(id(c))
            for rr in range(row,row+rs):
                for cc in range(col,col+cs):
                    covered.add((rr,cc))
            tag = 'th' if c.get('column_header') or c.get('row_header') else 'td'
            attrs = (f' rowspan="{rs}"' if rs>1 else '')+(f' colspan="{cs}"' if cs>1 else '')
            text = html.escape(c.get('text','')).replace('\n','<br>')
            lines.append(f'<{tag}{attrs}>{text}</{tag}>')
        lines.append('</tr>')
    lines.append('</table>')
    # Conflicting source coordinates must never silently drop a cell.
    for c in cells:
        if id(c) not in emitted_cells:
            lines.append('<p>'+html.escape(c.get('text','')).replace('\n','<br>')+'</p>')
    return '\n'.join(lines)


def render(doc, decisions):
    excluded = {x['self_ref'] for x in decisions}
    seen, entries, chunks, debug = set(), [], [], []
    stats = Counter()
    offset = 0
    last_page = None
    def emit(item, content, kind):
        nonlocal offset,last_page
        content = '\n'.join(x.rstrip() for x in content.splitlines()).strip('\n')
        if not content: return
        if chunks: offset += 2
        prov = item.get('prov', [])
        p = prov[0] if prov else {}
        entries.append(dict(output_order=len(entries),source_ref=item['self_ref'],source_type=item['self_ref'].split('/')[1],
            page_no=p.get('page_no'),bbox=p.get('bbox'),prov=prov,render_type=kind,
            markdown_char_start=offset,markdown_char_end=offset+len(content)))
        offset += len(content)
        chunks.append(content)
        if p.get('page_no') != last_page:
            debug.append(f"<!-- PAGE {p.get('page_no')} -->")
            last_page = p.get('page_no')
        debug.append('<!-- source_ref: '+item['self_ref']+' -->\n\n'+content)
    def visit(ref):
        if ref in seen:
            stats['skipped_duplicate_refs'] += 1
            return
        seen.add(ref)
        item = doc.refs.get(ref)
        if not item:
            doc.warnings.append('unresolved ref: '+ref)
            return
        if ref in excluded:
            stats['skipped_furniture_refs'] += 1
            return
        kind = ref.split('/')[1]
        stats['rendered_'+kind] += 1
        if kind=='texts': emit(item,text_markdown(item),item.get('label'))
        elif kind=='tables':
            content = table_html(item)
            if '</table>\n<p>' in content:
                doc.warnings.append('complex_table_rendered_lossily=true; conflicting cell retained after table: '+ref)
            emit(item,content,'html_table')
        elif kind=='pictures':
            uri = (item.get('image') or {}).get('uri')
            if uri: emit(item,f'![image]({uri})','picture')
            else:
                debug.append('<!-- picture omitted: ref='+ref+' -->')
                doc.warnings.append('picture has no image URI: '+ref)
        for field in ('captions','children','footnotes'):
            for child in item.get(field,[]): visit(child['$ref'])
    for child in doc.data.get('body',{}).get('children',[]): visit(child['$ref'])
    unreachable = [ref for ref,item in doc.refs.items() if ref not in seen and ref not in excluded and not ref.startswith('#/groups/')]
    if unreachable: doc.warnings.append('body-unreachable items: '+', '.join(unreachable))
    return '\n\n'.join(chunks)+'\n', '\n\n'.join(debug)+'\n', entries, dict(stats)
