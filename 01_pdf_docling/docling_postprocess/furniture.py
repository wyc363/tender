"""Document-local furniture inference; no document-specific text or coordinates."""
import math
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from .loader import display


@dataclass(frozen=True)
class Config:
    edge_band: float = .15
    max_anchor_band: float = .25
    position_tolerance: float = .018
    horizontal_gap_ratio: float = .10
    min_repeat_pages: int = 3
    anchored_repeat_fraction: float = .10
    unanchored_repeat_fraction: float = .65
    isolation_gap_ratio: float = .012
    max_block_height_ratio: float = .06
    max_text_chars: int = 400
    allow_unanchored: bool = True

    def __post_init__(self):
        for name in ('edge_band','max_anchor_band','position_tolerance','horizontal_gap_ratio',
                     'anchored_repeat_fraction','unanchored_repeat_fraction',
                     'isolation_gap_ratio','max_block_height_ratio'):
            upper = 1 if 'fraction' in name else .5
            if not 0 < getattr(self,name) <= upper:
                raise ValueError('Invalid configuration: '+name)
        if self.min_repeat_pages < 2 or self.max_text_chars < 1:
            raise ValueError('min_repeat_pages >= 2 and max_text_chars >= 1 required')
        if self.max_anchor_band < self.edge_band:
            raise ValueError('max_anchor_band must be >= edge_band')


def pattern(text, kind):
    """Whitespace and explicit page counters only; keep arbitrary numbers."""
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'第\s*[0-9０-９]+\s*页', '第{page}页', text)
    text = re.sub(r'共\s*[0-9０-９]+\s*页', '共{pages}页', text)
    text = re.sub(r'\bpage\s+[0-9]+(?:\s*(?:of|/)\s*[0-9]+)?\b', 'Page {page}', text, flags=re.I)
    if kind == 'page_footer' and re.fullmatch(r'[-–—]?\s*\d+\s*[-–—]?',text):
        return '{page}'
    return text


def repair(doc, config=None):
    cfg = config or Config()
    decisions, suspicious = [], []
    anchors, templates, groups, obstacles = (defaultdict(list) for _ in range(4))
    candidates, native = [], set()
    pages = sorted(int(p) for p in doc.data.get('pages',{}))
    total = max(1,len(pages))

    def sample(item,p,box,kind):
        return dict(item=item,prov=p,box=box,kind=kind,
                    key=pattern(display(item),kind),y=(box[1]+box[3])/2/box[5])

    for item in doc.refs.values():
        label = item.get('label')
        is_native = item.get('content_layer') == 'furniture' or label in ('page_header','page_footer')
        if is_native:
            native.add(item['self_ref'])
            decisions.append(dict(self_ref=item['self_ref'],decision='exclude_as_furniture',
                source='docling_native',furniture_type=label,confidence=1.0,
                original_text=display(item),original_label=label,
                original_content_layer=item.get('content_layer'),prov=item.get('prov',[]),reasons=['native_furniture']))
        for p in item.get('prov',[]):
            box=doc.box(p)
            if not box: continue
            if is_native and label in ('page_header','page_footer'):
                s=sample(item,p,box,label)
                anchors[p['page_no'],label].append(s)
                templates[label,s['key']].append(s)
                groups[label,s['key']].append(s)
            elif not is_native and not item['self_ref'].startswith('#/groups/'):
                obstacles[p['page_no']].append((item,box))

    def edge_distance(box,kind):
        return box[1]/box[5] if kind=='page_header' else 1-box[3]/box[5]

    def protected(item,p,box):
        parent=item.get('parent',{}).get('$ref')
        visited=set()
        while parent and parent not in visited:
            visited.add(parent)
            if parent.startswith('#/tables/'): return True
            parent=doc.refs.get(parent,{}).get('parent',{}).get('$ref')
        l,t,r,b,w,h=box
        area=max(.001,(r-l)*(b-t))
        for table in doc.page_to_tables.get(p['page_no'],[]):
            for tp in table.get('prov',[]):
                if tp['page_no'] != p['page_no']: continue
                tb=doc.box(tp)
                if tb and max(0,min(r,tb[2])-max(l,tb[0]))*max(0,min(b,tb[3])-max(t,tb[1]))/area>.3:
                    return True
        return False

    for item in doc.data.get('texts',[]):
        if item['self_ref'] in native or item.get('content_layer','body')!='body': continue
        if item.get('label') not in ('text','caption','section_header','list_item'): continue
        for p in item.get('prov',[]):
            box=doc.box(p)
            if not box: continue
            for kind in ('page_header','page_footer'):
                band=cfg.edge_band
                for a in anchors[p['page_no'],kind]:
                    band=max(band,min(cfg.max_anchor_band,edge_distance(a['box'],kind)+cfg.position_tolerance))
                # Learn the same template's band from other pages as well.
                for a in templates[kind,pattern(display(item),kind)]:
                    band=max(band,min(cfg.max_anchor_band,edge_distance(a['box'],kind)+cfg.position_tolerance))
                if edge_distance(box,kind)>band: continue
                s=sample(item,p,box,kind)
                s['protected']=protected(item,p,box)
                candidates.append(s)
                if not s['protected']: groups[kind,s['key']].append(s)

    def nearby(s, pool):
        return [q for q in pool if abs(s['y']-q['y']) <= cfg.position_tolerance]

    # Index complete native text with insignificant whitespace removed. This
    # handles a native header that is split differently on another page.
    compact_templates=defaultdict(list)
    for (kind,key),samples in templates.items():
        compact_templates[kind,re.sub(r'\s+','',key)].extend(samples)

    def recomposed_template(s, adjacent):
        for a in adjacent:
            pair=sorted([s,a],key=lambda q:q['box'][0])
            joined=pattern(' '.join(display(q['item']) for q in pair),s['kind'])
            matches=nearby(s,compact_templates[s['kind'],re.sub(r'\s+','',joined)])
            # Require multiple ORIGINAL native examples, not inferred examples.
            other_pages={q['prov']['page_no'] for q in matches if q['prov']['page_no']!=s['prov']['page_no']}
            if len(other_pages)>=2:
                return a,matches
        return None,[]

    def geometry(s,a):
        l,t,r,b,w,h=s['box']; al,at,ar,ab,_,_=a['box']
        overlap=max(0,min(b,ab)-max(t,at))/max(.001,min(b-t,ab-at))
        gap=max(l-ar,al-r,0)
        horizontal_overlap=max(0,min(r,ar)-max(l,al))/max(.001,min(r-l,ar-al))
        return (overlap>=.60 and horizontal_overlap<=.15 and
                abs((t+b-at-ab)/2)<=max(3,.015*h) and gap<=max(24,cfg.horizontal_gap_ratio*w))

    def repetition(s):
        pool=nearby(s,groups[s['kind'],s['key']])
        distinct={q['prov']['page_no'] for q in pool}
        if s['key']=='{page}':
            offsets=defaultdict(set)
            for q in pool:
                n=re.search(r'\d+',display(q['item']))
                if n: offsets[int(n.group())-q['prov']['page_no']].add(q['prov']['page_no'])
            n=re.search(r'\d+',display(s['item']))
            distinct=offsets[int(n.group())-s['prov']['page_no']] if n else set()
        ratio=len(distinct)/total
        parity=s['prov']['page_no']%2
        parity_pages={p for p in pages if p%2==parity}
        parity_hits=distinct & parity_pages
        if len(parity_hits)>=cfg.min_repeat_pages:
            ratio=max(ratio,len(parity_hits)/max(1,len(parity_pages)))
        return len(distinct),ratio

    def isolated(s):
        l,t,r,b,w,h=s['box']
        interior=[]
        for other,box in obstacles[s['prov']['page_no']]:
            if other['self_ref']==s['item']['self_ref']: continue
            ol,ot,orr,ob,_,_=box
            if max(0,min(b,ob)-max(t,ot))>0:
                if orr>l and ol<r: return False
                continue
            if s['kind']=='page_header':
                if ob<=t: return False
                if ot>=b: interior.append((ot-b)/h)
            else:
                if ot>=b: return False
                if ob<=t: interior.append((t-ob)/h)
        return bool(interior) and min(interior)>=cfg.isolation_gap_ratio

    excluded=set(native)
    pending=list(candidates)
    # Recover chains of fragments without depending on array order.
    for iteration in range(len(candidates)+1):
        next_pending=[]
        changed=False
        for s in pending:
            item,p,box,kind=s['item'],s['prov'],s['box'],s['kind']
            if item['self_ref'] in excluded: continue
            count,ratio=repetition(s)
            repeat=count>=max(cfg.min_repeat_pages,math.ceil(total*cfg.anchored_repeat_fraction))
            adjacent=[a for a in anchors[p['page_no'],kind] if geometry(s,a)]
            anchor=next(iter(adjacent),None)
            recomposed_anchor,recomposed_matches=recomposed_template(s,adjacent)
            native_matches=nearby(s,templates[kind,s['key']])
            native_pages={q['prov']['page_no'] for q in native_matches}
            short=bool(s['key']) and len(display(item))<=cfg.max_text_chars and (box[3]-box[1])/box[5]<=cfg.max_block_height_ratio
            safe=short and not s['protected'] and len(item.get('prov',[]))==1
            route=None
            if safe and item.get('label')=='text' and anchor and repeat:
                route='adjacent_to_furniture'
            elif safe and item.get('label')=='text' and recomposed_anchor:
                route='native_fragment_recomposition'
                anchor=recomposed_anchor
                native_matches=recomposed_matches
            elif safe and len(native_pages)>=2 and count>=cfg.min_repeat_pages:
                route='native_template_match'
            elif (safe and item.get('label')=='text' and cfg.allow_unanchored and
                  edge_distance(box,kind)<=cfg.edge_band and count>=cfg.min_repeat_pages and
                  ratio>=cfg.unanchored_repeat_fraction and isolated(s)):
                route='repeated_isolated_margin'
            if route:
                l,t,r,b,w,h=box
                decisions.append(dict(self_ref=item['self_ref'],decision='exclude_as_furniture',source='inferred',
                    furniture_type=kind+'_fragment',original_text=display(item),original_label=item.get('label'),
                    original_content_layer=item.get('content_layer'),page_no=p['page_no'],bbox=p['bbox'],prov=item.get('prov',[]),
                    confidence=.99 if route=='adjacent_to_furniture' else .95,
                    reasons=[route,'page_relative_geometry','repeated_across_pages'],
                    anchor_ref=anchor['item']['self_ref'] if anchor else None,
                    native_template_refs=[q['item']['self_ref'] for q in native_matches],
                    unique_page_count=count,repeat_coverage=ratio,normalized_bbox=[l/w,t/h,r/w,b/h],pattern=s['key']))
                excluded.add(item['self_ref']); anchors[p['page_no'],kind].append(s)
                changed=True
            else: next_pending.append(s)
        pending=next_pending
        if not changed: break

    for s in pending:
        item,p=s['item'],s['prov']
        if item['self_ref'] in excluded: continue
        count,ratio=repetition(s)
        reasons=['insufficient_combined_evidence']
        if s['protected']: reasons.append('table_content_protected')
        if item.get('label')!='text': reasons.append('non_text_label_protected_without_native_template')
        if not anchors[p['page_no'],s['kind']]: reasons.append('candidate_without_anchor')
        suspicious.append(dict(self_ref=item['self_ref'],page_no=p['page_no'],bbox=p['bbox'],
            original_text=display(item),original_label=item.get('label'),decision='retain_for_review',
            furniture_type=s['kind']+'_candidate',unique_page_count=count,repeat_coverage=ratio,reasons=reasons))
    doc.furniture_config=asdict(cfg)
    return decisions,suspicious
