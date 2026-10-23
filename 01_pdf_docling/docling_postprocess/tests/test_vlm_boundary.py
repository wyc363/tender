import json
import tempfile
import unittest
from pathlib import Path

from docling_postprocess.vlm_boundary_prompt import active_blocks, build_user_prompt
from docling_postprocess.vlm_boundary_schema import validate_boundary
from docling_postprocess.vlm_boundary_structure import run_boundary_pages
from docling_postprocess.vlm_client import VLMClientResult
from docling_postprocess.vlm_config import ParserVLMSettings


def settings():
    return ParserVLMSettings("https://workspace.cn-beijing.example/v1", "secret", "qwen3.7-flash",
                             0, 2500, False, 120, 1, 1572864, 1)


class BoundarySchemaTests(unittest.TestCase):
    def setUp(self):
        self.active=[{"block_id":"P0001-B001","kind":"text"},{"block_id":"P0001-T001","kind":"table"}]
    def valid(self):
        return {"schema_version":"A2.1-boundary-v1","page_no":1,"page_type":"mixed","elements":[
            {"block_id":"P0001-B001","boundary_role":"soft"},{"block_id":"P0001-T001","boundary_role":"none"}]}
    def errors(self, value, covered=None, finish=None): return validate_boundary(value,1,self.active,covered,finish)
    def test_normal_hard_soft_none_and_all_preserved(self): self.assertEqual(self.errors(self.valid()),[])
    def test_unknown_block(self):
        v=self.valid();v['elements'][0]['block_id']='X';self.assertTrue(any('UNKNOWN' in x for x in self.errors(v)))
    def test_duplicate_block(self):
        v=self.valid();v['elements'][1]['block_id']=v['elements'][0]['block_id'];self.assertIn('ELEMENTS_DUPLICATE',self.errors(v))
    def test_missing_block(self):
        v=self.valid();v['elements'].pop();self.assertTrue(any('MISSING' in x for x in self.errors(v)))
    def test_covered_block_returned(self):
        v=self.valid();v['elements'].append({'block_id':'P0001-X999','boundary_role':'none'})
        self.assertTrue(any('COVERED_BY_TABLE' in x for x in self.errors(v,{'P0001-X999'})))
    def test_table_boundary_must_be_none(self):
        v=self.valid();v['elements'][1]['boundary_role']='hard';self.assertIn('TABLE_BOUNDARY_NOT_NONE: P0001-T001',self.errors(v))
    def test_invalid_boundary_role_and_no_ignore(self):
        v=self.valid();v['elements'][0]['boundary_role']='ignore';e=self.errors(v)
        self.assertTrue(any('BOUNDARY_ROLE_INVALID' in x for x in e));self.assertTrue(any('IGNORE_NOT_ALLOWED' in x for x in e))
    def test_extra_fields(self):
        v=self.valid();v['elements'][0]['text']='forbidden';self.assertTrue(any('FIELDS_INVALID' in x for x in self.errors(v)))
    def test_page_type_invalid(self):
        v=self.valid();v['page_type']='text';self.assertIn('PAGE_TYPE_INVALID',self.errors(v))
    def test_finish_reason_length(self): self.assertTrue(any('INVALID_TRUNCATED' in x for x in self.errors(self.valid(),finish='length')))


class BoundaryPromptTests(unittest.TestCase):
    def test_request_excludes_parser_label_flags_and_html(self):
        page={"blocks":[{"block_id":"P0001-T001","kind":"table","bbox_norm":[0,0,1,1],
              "text":"preview","parser_label":"table","flags":[],"table_html":"<table>secret</table>"}]}
        prompt=build_user_prompt(1,active_blocks(page))
        self.assertNotIn('parser_label',prompt);self.assertNotIn('flags',prompt);self.assertNotIn('secret',prompt)


class BoundaryStructureTests(unittest.TestCase):
    def fixture(self, root):
        image=root/'page.jpg';image.write_bytes(b'jpeg')
        records=[{"page_no":1,"blocks":[
          {"block_id":"P0001-B001","kind":"text","bbox_norm":[0,0,1,1],"text":"条款","parser_label":"section_header","flags":[]},
          {"block_id":"P0001-T001","kind":"table","bbox_norm":[0,1,1,2],"text":"表格","parser_label":"table","flags":[]},
          {"block_id":"P0001-B999","kind":"text","bbox_norm":[0,1,1,2],"text":"重复","parser_label":"text","flags":["COVERED_BY_TABLE"]}]}]
        manifest={"images":[{"page_no":1,"path":str(image),"width_px":10,"height_px":10,"bytes":4}]}
        return records,manifest
    def response(self, finish='stop'):
        content=json.dumps({"schema_version":"A2.1-boundary-v1","page_no":1,"page_type":"mixed","elements":[
          {"block_id":"P0001-B001","boundary_role":"soft"},{"block_id":"P0001-T001","boundary_role":"none"}]})
        return VLMClientResult(content,{"id":"x"},{"prompt_tokens":10,"input_tokens":10,"completion_tokens":8,"output_tokens":8,"total_tokens":18},.1,'json_schema_strict',True,[],finish)
    def test_success_and_request_audit(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);records,manifest=self.fixture(root)
            rows,usage=run_boundary_pages(records,manifest,[1],root/'out',settings(),lambda *_:self.response())
            audit=json.loads((root/'out/request_audit/page_0001.json').read_text('utf-8'))
        self.assertEqual(rows[0]['status'],'SUCCESS');self.assertEqual(usage['total_tokens'],18)
        self.assertNotIn('parser_label',json.dumps(audit['blocks_sent']));self.assertEqual(len(rows[0]['elements']),2)
    def test_truncated_status(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);records,manifest=self.fixture(root)
            rows,_=run_boundary_pages(records,manifest,[1],root/'out',settings(),lambda *_:self.response('length'))
        self.assertEqual(rows[0]['status'],'INVALID_TRUNCATED')
    def test_old_schema_resume_is_not_reused(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);records,manifest=self.fixture(root);out=root/'out';out.mkdir()
            (out/'raw_responses').mkdir();(out/'request_audit').mkdir()
            old={"page_no":1,"status":"SUCCESS","model":"qwen3.7-flash","reading_order":["P0001-B001","P0001-T001"],"classifications":[]}
            (out/'page_structures.jsonl').write_text(json.dumps(old)+'\n',encoding='utf-8')
            (out/'raw_responses/page_0001.json').write_text('{}');(out/'request_audit/page_0001.json').write_text('{}')
            calls=[]
            rows,usage=run_boundary_pages(records,manifest,[1],out,settings(),lambda *_:(calls.append(1) or self.response()),resume=True)
        self.assertEqual(len(calls),1);self.assertEqual(usage['pages_reused_from_resume'],0);self.assertEqual(rows[0]['schema_version'],'A2.1-boundary-v1')


if __name__ == '__main__': unittest.main()
