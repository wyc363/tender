import json
import tempfile
import unittest
from pathlib import Path

from docling_postprocess.vlm_client import VLMClientResult
from docling_postprocess.vlm_config import ParserVLMSettings
from docling_postprocess.vlm_selector_prompt import active_blocks, build_user_prompt
from docling_postprocess.vlm_selector_report import render_toc_preview
from docling_postprocess.vlm_selector_schema import validate_selector
from docling_postprocess.vlm_selector_structure import run_selector_pages


def settings():
    return ParserVLMSettings('https://workspace.cn-beijing.example/v1','secret','qwen3.7-flash',0,2500,False,120,1,1572864,1)


class SelectorSchemaTests(unittest.TestCase):
    def setUp(self): self.active=[{'block_id':'P0001-B001','kind':'text'},{'block_id':'P0001-T001','kind':'table'}]
    def valid(self): return {'schema_version':'A2.2-boundary-selector-v1','page_no':1,'page_type':'mixed','elements':[
      {'block_id':'P0001-B001','chunk_boundary':True},{'block_id':'P0001-T001','chunk_boundary':False}]}
    def errors(self,v,covered=None,finish=None): return validate_selector(v,1,self.active,covered,finish)
    def test_normal_elements(self): self.assertEqual(self.errors(self.valid()),[])
    def test_boundary_must_be_boolean(self):
        v=self.valid();v['elements'][0]['chunk_boundary']=1;self.assertTrue(any('NOT_BOOLEAN' in x for x in self.errors(v)))
    def test_table_true_rejected(self):
        v=self.valid();v['elements'][1]['chunk_boundary']=True;self.assertTrue(any('TABLE_BOUNDARY_TRUE' in x for x in self.errors(v)))
    def test_toc_true_rejected(self):
        v=self.valid();v['page_type']='toc';self.assertTrue(any('TOC_BOUNDARY_TRUE' in x for x in self.errors(v)))
    def test_unknown(self):
        v=self.valid();v['elements'][0]['block_id']='X';self.assertTrue(any('UNKNOWN' in x for x in self.errors(v)))
    def test_duplicate(self):
        v=self.valid();v['elements'][1]['block_id']='P0001-B001';self.assertIn('ELEMENTS_DUPLICATE',self.errors(v))
    def test_missing(self):
        v=self.valid();v['elements'].pop();self.assertTrue(any('MISSING' in x for x in self.errors(v)))
    def test_covered(self):
        v=self.valid();v['elements'].append({'block_id':'P0001-X999','chunk_boundary':False})
        self.assertTrue(any('COVERED_BY_TABLE' in x for x in self.errors(v,{'P0001-X999'})))
    def test_extra_fields(self):
        v=self.valid();v['elements'][0]['text']='x';self.assertTrue(any('FIELDS_INVALID' in x for x in self.errors(v)))
    def test_finish_reason_length(self): self.assertTrue(any('INVALID_TRUNCATED' in x for x in self.errors(self.valid(),finish='length')))
    def test_schema_version_isolated(self):
        v=self.valid();v['schema_version']='A2.1-boundary-v1';self.assertIn('SCHEMA_VERSION_INVALID',self.errors(v))


class SelectorPromptAndPreviewTests(unittest.TestCase):
    def test_parser_label_not_sent(self):
        p={'blocks':[{'block_id':'P0001-B001','kind':'text','bbox_norm':[0,0,1,1],'text':'原文','parser_label':'section_header','flags':[]}]}
        self.assertNotIn('parser_label',build_user_prompt(1,active_blocks(p)))
    def test_table_html_not_sent(self):
        p={'blocks':[{'block_id':'P0001-T001','kind':'table','bbox_norm':[0,0,1,1],'text':'预览','table_html':'<table>SECRET</table>','flags':[]}]}
        self.assertNotIn('SECRET',build_user_prompt(1,active_blocks(p)))
    def test_toc_preview_reads_a1_rows(self):
        block={'table_html':'<table><tr><td>第1章</td><td>5</td></tr><tr><td></td><td>招标公告</td></tr></table>','text':'fallback'}
        self.assertEqual(render_toc_preview(block),['第1章 5','招标公告'])
    def test_toc_preview_has_no_model_or_ocr_dependency(self):
        self.assertEqual(render_toc_preview({'table_html':'','text':'A | B\nC | D'}),['A B','C D'])
    def test_original_source_unchanged(self):
        block={'table_html':'<table><tr><td>原始文字</td></tr></table>','text':'原始'};before=dict(block)
        render_toc_preview(block);self.assertEqual(block,before)


class SelectorResumeTests(unittest.TestCase):
    def test_old_a21_resume_not_reused(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);img=root/'p.jpg';img.write_bytes(b'jpeg');out=root/'out';out.mkdir();(out/'raw_responses').mkdir();(out/'request_audit').mkdir()
            records=[{'page_no':1,'blocks':[{'block_id':'P0001-B001','kind':'text','bbox_norm':[0,0,1,1],'text':'标题','parser_label':'text','flags':[]}]}]
            manifest={'images':[{'page_no':1,'path':str(img),'width_px':1,'height_px':1,'bytes':4}]}
            old={'schema_version':'A2.1-boundary-v1','prompt_version':'A2.1-boundary-v1','page_no':1,'status':'SUCCESS','model':'qwen3.7-flash','elements':[{'block_id':'P0001-B001','boundary_role':'hard'}]}
            (out/'page_structures.jsonl').write_text(json.dumps(old)+'\n');(out/'raw_responses/page_0001.json').write_text('{}');(out/'request_audit/page_0001.json').write_text('{}')
            content=json.dumps({'schema_version':'A2.2-boundary-selector-v1','page_no':1,'page_type':'content','elements':[{'block_id':'P0001-B001','chunk_boundary':False}]})
            response=VLMClientResult(content,{'id':'x'},{'prompt_tokens':1,'input_tokens':1,'completion_tokens':1,'output_tokens':1,'total_tokens':2},.1,'json_schema_strict',True,[],'stop')
            calls=[];rows,usage=run_selector_pages(records,manifest,[1],out,settings(),lambda *_:(calls.append(1) or response),resume=True)
        self.assertEqual(len(calls),1);self.assertEqual(usage['pages_reused_from_resume'],0);self.assertEqual(rows[0]['schema_version'],'A2.2-boundary-selector-v1')


if __name__=='__main__': unittest.main()
