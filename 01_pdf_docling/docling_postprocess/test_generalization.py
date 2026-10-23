import copy
import json
import tempfile
import unittest
import subprocess
import sys
from pathlib import Path
from .loader import Document
from .furniture import Config, repair, pattern
from .cli import convert


def document(n=6, y=.06, footer=False, mixed=False, label='text', content=None):
    data=dict(pages={},texts=[],body=dict(children=[]))
    for page in range(1,n+1):
        w,h=(1200,800) if mixed and page%2 else (600,900)
        data['pages'][str(page)]=dict(size=dict(width=w,height=h))
        def add(text,top,bottom,left=.10,right=.9,item_label='text'):
            ref='#/texts/'+str(len(data['texts']))
            item=dict(self_ref=ref,parent={'$ref':'#/body'},content_layer='body',label=item_label,text=text,
                prov=[dict(page_no=page,bbox=dict(l=left*w,r=right*w,t=top*h,b=bottom*h,coord_origin='TOPLEFT'))])
            data['texts'].append(item); data['body']['children'].append({'$ref':ref})
        add(content(page) if content else 'A different running header',1-y-.01 if footer else y,
            1-y if footer else y+.01,.50 if page%2 else .05,.95 if page%2 else .45,label)
        add('Body paragraph '+str(page),.30,.70)
    return data


def inferred(data,cfg=None):
    return [x for x in repair(Document(data),cfg)[0] if x['source']=='inferred']


class GeneralizationTests(unittest.TestCase):
    def test_no_native_anchor_and_mixed_page_sizes(self):
        self.assertEqual(len(inferred(document(mixed=True))),6)

    def test_different_positions_and_lengths(self):
        for y in [.02,.075,.13]:
            for title in ['Short', 'A considerably longer organization header '*6]:
                self.assertEqual(len(inferred(document(y=y,content=lambda p:title))),6)

    def test_odd_even_headers(self):
        self.assertEqual(len(inferred(document(content=lambda p:'Left title' if p%2 else 'Right title'))),6)

    def test_variable_page_counters(self):
        for title in [lambda p:f'Report — Page {p} of 6',lambda p:f'第 {p} 页 共 6 页',lambda p:str(p+10)]:
            self.assertEqual(len(inferred(document(footer=True,content=title))),6)

    def test_repeated_non_page_numbers_retained(self):
        self.assertEqual(len(inferred(document(footer=True,content=lambda p:'100'))),0)
        self.assertEqual(pattern('Clause 12','page_header'),'Clause 12')

    def test_three_page_document(self):
        self.assertEqual(len(inferred(document(n=3))),3)

    def test_one_and_two_pages_retain_uncertain(self):
        for n in [1,2]: self.assertEqual(inferred(document(n=n)),[])

    def test_middle_repeated_content_retained(self):
        self.assertEqual(inferred(document(y=.40)),[])

    def test_real_headings_and_list_items_retained(self):
        for label in ['section_header','list_item','caption']:
            self.assertEqual(inferred(document(label=label)),[])

    def test_different_text_each_page_retained(self):
        self.assertEqual(inferred(document(content=lambda p:'Different chapter '+str(p))),[])

    def test_nearby_body_retained(self):
        d=document()
        for x in d['texts'][1::2]:
            x['prov'][0]['bbox']['t']=.071*900
        self.assertEqual(inferred(d),[])

    def test_repeated_table_heading_protected(self):
        d=document(); d['tables']=[]
        for x in d['texts'][::2]:
            d['tables'].append(dict(self_ref='#/tables/'+str(len(d['tables'])),prov=copy.deepcopy(x['prov'])))
        self.assertEqual(inferred(d),[])

    def test_native_template_recovers_heading_label(self):
        for y in [.06,.20]:
            d=document(label='section_header',y=y)
            for x in d['texts'][:4:2]:
                x.update(label='page_header',content_layer='furniture')
            self.assertEqual(len(inferred(d)),4)

    def test_chain_of_fragments(self):
        d=document()
        for page in range(1,7):
            x=d['texts'][(page-1)*2]
            x['prov'][0]['bbox'].update(l=300,r=500)
            for left,right,layer,text in [(125,290,'body','middle fragment'),(0,110,'furniture','anchor')]:
                ref='#/texts/'+str(len(d['texts']))
                d['texts'].append(dict(self_ref=ref,label='page_header' if layer=='furniture' else 'text',
                    content_layer=layer,text=text,prov=[dict(page_no=page,bbox=dict(l=left,r=right,t=54,b=63,coord_origin='TOPLEFT'))]))
        result=inferred(d,Config(allow_unanchored=False))
        self.assertEqual(len(result),12)

    def test_single_fragment_recomposed_from_native_templates(self):
        # Deliberately use unrelated text: no hard-coded year/header string.
        d=document(n=4,content=lambda p:'Volume Seven')
        for x in d['texts'][:6:2]:
            x.update(label='page_header',content_layer='furniture')
        fragment=d['texts'][6]
        fragment['text']='Volume'
        fragment['prov'][0]['bbox'].update(l=60,r=200)
        d['texts'].append(dict(self_ref='#/texts/8',text='Seven',content_layer='furniture',label='page_header',
            prov=[dict(page_no=4,bbox=dict(l=205,r=290,t=54,b=63,coord_origin='TOPLEFT'))]))
        result=inferred(d)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['reasons'][0],'native_fragment_recomposition')
        self.assertEqual(result[0]['self_ref'],'#/texts/6')
        # Without known complete templates, a one-off adjacent item stays.
        for x in d['texts'][:6:2]: x['text']='Unrelated header'
        self.assertEqual(inferred(d),[])

    def test_batch_command(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in ['one','nested/two']:
                folder=root/'input'/name; folder.mkdir(parents=True)
                (folder/'document.json').write_text(json.dumps(document()),encoding='utf-8')
            result=subprocess.run([sys.executable,'-m','docling_postprocess.cli','--input',str(root/'input'),
                '--output-dir',str(root/'output')],cwd=Path(__file__).resolve().parent.parent,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            report=json.loads((root/'output/batch_report.json').read_text(encoding='utf-8'))
            self.assertEqual([x['status'] for x in report],['PASS','PASS'])
            self.assertTrue((root/'output/nested/two/cleaned.md').exists())

    def test_invalid_config(self):
        with self.assertRaises(ValueError): Config(edge_band=.60)
        with self.assertRaises(ValueError): Config(min_repeat_pages=1)

    def test_adaptive_anchor_outside_default_band(self):
        d=document(y=.20)
        for p in range(1,7):
            ref='#/texts/'+str(len(d['texts']))
            d['texts'].append(dict(self_ref=ref,label='page_header',content_layer='furniture',text='Known anchor',
                prov=[dict(page_no=p,bbox=dict(l=0,r=20,t=180,b=189,coord_origin='TOPLEFT'))]))
            d['texts'][(p-1)*2]['prov'][0]['bbox'].update(l=25,r=500)
        self.assertEqual(len(inferred(d)),6)

    def test_configurable_margin_and_conservative_mode(self):
        self.assertEqual(inferred(document(y=.18)),[])
        self.assertEqual(len(inferred(document(y=.18),Config(edge_band=.20))),6)
        self.assertEqual(inferred(document(),Config(allow_unanchored=False)),[])

    def test_conversion_unrelated_document(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); src=root/'input.json'
            src.write_text(json.dumps(document()),encoding='utf-8')
            original=src.read_bytes()
            report=convert(src,root/'out')
            self.assertTrue(all(report['checks'].values()))
            self.assertEqual(src.read_bytes(),original)
            self.assertNotIn('A different running header',(root/'out/cleaned.md').read_text(encoding='utf-8'))
            self.assertNotIn('page27_28_required_refs_retained',report['checks'])


if __name__=='__main__': unittest.main()
