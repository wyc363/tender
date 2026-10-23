import copy
import unittest
from .loader import Document
from .furniture import Config, repair
from .renderer import render,text_markdown,table_html


class Tests(unittest.TestCase):
    def test_list(self):
        self.assertEqual(text_markdown(dict(label='list_item',text='（ 1 ）原文',enumerated=False,marker='')),'- （ 1 ）原文')
        for text in ['设计人需配合','3.1.1 设计人需配合']:
            self.assertEqual(text_markdown(dict(label='list_item',text=text,enumerated=True,marker='3.1.1')),'- 3.1.1 设计人需配合')

    def fixture(self,origin='BOTTOMLEFT',footer=False):
        texts=[]
        for page in range(1,6):
            for label,layer,text,l,r in [('page_footer' if footer else 'page_header','furniture','任意锚点',79.6,241.833),('text','body','任意重复片段',244.05,433.27),('section_header','body','真实标题',79,400)]:
                top,bottom=(43.319,52.319) if not footer else (789,799)
                if label=='section_header': top,bottom=70,82
                bbox=dict(l=l,r=r,t=842.25-top if origin=='BOTTOMLEFT' else top,b=842.25-bottom if origin=='BOTTOMLEFT' else bottom,coord_origin=origin)
                texts.append(dict(self_ref=f'#/texts/{len(texts)}',label=label,content_layer=layer,text=text,prov=[dict(page_no=page,bbox=bbox)],children=[]))
        return dict(texts=texts,pages={str(i):dict(size=dict(width=595.5,height=842.25)) for i in range(1,6)},body=dict(children=[{'$ref':x['self_ref']} for x in texts]))

    def test_furniture_and_coordinates(self):
        for origin in ['BOTTOMLEFT','TOPLEFT']:
            for footer in [False,True]:
                data=self.fixture(origin,footer); before=copy.deepcopy(data)
                decisions,_=repair(Document(data))
                self.assertEqual(len(decisions),10)
                self.assertFalse(any(x['original_label']=='section_header' for x in decisions))
                self.assertEqual(data,before)

    def test_no_anchor_no_deletion(self):
        data=self.fixture()
        data['texts']=[x for x in data['texts'] if x['content_layer']=='body']
        self.assertEqual(repair(Document(data),Config(allow_unanchored=False))[0],[])

    def test_no_repeat_no_deletion(self):
        data=self.fixture()
        for i,x in enumerate(data['texts']):
            if x['content_layer']=='body': x['text']=str(i)
        self.assertEqual(len(repair(Document(data))[0]),5)

    def test_order_duplicates_cross_page_and_map(self):
        data=self.fixture(); data['body']['children']=[{'$ref':'#/texts/4'},{'$ref':'#/texts/1'},{'$ref':'#/texts/4'}]
        data['texts'][4]['text']='考察的责'; data['texts'][1]['text']='任、风险和费用'
        clean,_,entries,stats=render(Document(data),[])
        self.assertEqual(clean,'考察的责\n\n任、风险和费用\n')
        self.assertEqual(stats['skipped_duplicate_refs'],1)
        self.assertEqual([clean[x['markdown_char_start']:x['markdown_char_end']] for x in entries],['考察的责','任、风险和费用'])

    def test_table_span(self):
        result=table_html(dict(data=dict(num_rows=2,num_cols=1,table_cells=[dict(start_row_offset_idx=0,start_col_offset_idx=0,row_span=2,col_span=1,text='A&B')])))
        self.assertIn('rowspan="2"',result)
        self.assertEqual(result.count('A&amp;B'),1)

    def test_missing_provenance(self):
        doc=Document(dict(texts=[dict(self_ref='#/texts/0',text='正文',label='text')],body=dict(children=[{'$ref':'#/texts/0'}])))
        self.assertTrue(doc.warnings)
        self.assertEqual(render(doc,[])[0],'正文\n')


if __name__=='__main__': unittest.main()
