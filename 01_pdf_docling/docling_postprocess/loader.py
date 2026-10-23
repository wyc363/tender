import json
from collections import defaultdict
from pathlib import Path


def display(item):
    return item.get('text') or item.get('orig') or ''


class Document:
    def __init__(self, data):
        self.data = data
        self.warnings = []
        self.refs = {}
        self.page_to_texts = defaultdict(list)
        self.page_to_tables = defaultdict(list)
        for kind in ('texts', 'tables', 'groups', 'pictures'):
            for item in data.get(kind, []):
                self.refs[item['self_ref']] = item
                if kind != 'groups' and not item.get('prov'):
                    self.warnings.append('missing provenance: ' + item['self_ref'])
                for p in item.get('prov', []):
                    if kind == 'texts':
                        self.page_to_texts[p['page_no']].append(item)
                    elif kind == 'tables':
                        self.page_to_tables[p['page_no']].append(item)
        self.group_ref_to_group = {x['self_ref']: x for x in data.get('groups', [])}
        self.table_ref_to_table = {x['self_ref']: x for x in data.get('tables', [])}

    @classmethod
    def load(cls, path):
        return cls(json.loads(Path(path).read_text(encoding='utf-8')))

    def box(self, prov):
        size = self.data.get('pages', {}).get(str(prov['page_no']), {}).get('size', {})
        b = prov.get('bbox', {})
        w, h = size.get('width'), size.get('height')
        if not w or not h or not b:
            return None
        if b.get('coord_origin') == 'BOTTOMLEFT':
            return b['l'], h-b['t'], b['r'], h-b['b'], w, h
        if b.get('coord_origin') == 'TOPLEFT':
            return b['l'], b['t'], b['r'], b['b'], w, h
        self.warnings.append('unknown coordinate origin: ' + str(b))
        return None
