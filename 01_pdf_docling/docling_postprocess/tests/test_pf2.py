import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from docling_postprocess.deterministic_structure import build_final_page,source_text_coverage
from docling_postprocess.goodmd_renderer import chunk_preflight,render_goodmd
from docling_postprocess.pf2_pipeline import build_reports,check_only,code_fingerprint,run_full
from docling_postprocess.vlm_config import ParserVLMSettings

def settings():return ParserVLMSettings('https://x.cn-beijing.example/v1','secret','qwen3.7-flash',0,2500,False,120,1,1572864,1)
def b(i,text='正文',kind='text',flags=None,html=''):
 return {'block_id':f'P0001-B{i:03d}','kind':kind,'text':text,'parser_label':'text','bbox_norm':[.1,.1+i*.02,.8,.11+i*.02],'flags':flags or [],'table_html':html}
def page(n=1,blocks=None):
 bs=[b(1)] if blocks is None else blocks;
 for i,x in enumerate(bs):x=x;x['block_id']=f'P{n:04d}-'+x['block_id'].split('-')[-1]
 return {'page_no':n,'blocks':bs}

class PF2CheckTests(unittest.TestCase):
 def test_fingerprint_has_frozen_files(self):self.assertEqual(len(code_fingerprint()['files']),6)
 def test_fingerprint_stable(self):self.assertEqual(code_fingerprint(),code_fingerprint())
 def _check(self):
  td=tempfile.TemporaryDirectory();root=Path(td.name);img=root/'p.jpg';img.write_bytes(b'x');records=[]
  for n in range(1,173):records.append(page(n,[] if n in (9,73,121) else [b(1)]))
  a1=root/'a1';a1.mkdir();(a1/'page_blocks.jsonl').write_text('\n'.join(json.dumps(x) for x in records));(a1/'page_images').mkdir();(a1/'page_images/manifest.json').write_text(json.dumps({'images':[{'page_no':n,'path':str(img)} for n in range(1,173)]}))
  return td,check_only(root/'x.pdf',root/'x.json',a1,root/'out',settings(),'A','B')
 def test_169_selection(self):td,r=self._check();self.assertEqual(r['active_pages'],169);td.cleanup()
 def test_empty_pages(self):td,r=self._check();self.assertEqual(r['empty_pages'],[9,73,121]);td.cleanup()
 def test_check_only_zero_api(self):td,r=self._check();self.assertEqual(r['api_calls'],0);td.cleanup()
 def test_api_key_redacted(self):td,r=self._check();self.assertEqual(r['api_key'],'CONFIGURED');td.cleanup()

class PF2PersistenceTests(unittest.TestCase):
 def run_case(self,root,fp='F'):
  img=root/'p.jpg';img.write_bytes(b'x');records=[page(1),page(2)];manifest={'images':[{'page_no':1,'path':str(img)},{'page_no':2,'path':str(img)}]}
  return run_full(records,manifest,root/'out',settings(),fp,True)
 def test_per_page_persistence(self):
  with tempfile.TemporaryDirectory() as d:self.run_case(Path(d));self.assertTrue((Path(d)/'out/page_state/page_0001.json').is_file())
 def test_final_jsonl_persistence(self):
  with tempfile.TemporaryDirectory() as d:self.run_case(Path(d));self.assertEqual(len((Path(d)/'out/final_page_structures.jsonl').read_text().splitlines()),2)
 def test_resume_same_fingerprint(self):
  with tempfile.TemporaryDirectory() as d:r=Path(d);self.run_case(r);*_,reused=self.run_case(r);self.assertEqual(reused,2)
 def test_resume_changed_fingerprint(self):
  with tempfile.TemporaryDirectory() as d:r=Path(d);self.run_case(r,'A');*_,reused=self.run_case(r,'B');self.assertEqual(reused,0)
 def test_failure_fallback(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d);img=r/'p.jpg';img.write_bytes(b'x');p=page(1,[b(1,'一、范围'),b(2,'/。'),b(3,'二、费用'),b(4,'/。')]);m={'images':[{'page_no':1,'path':str(img)}]}
   with patch('docling_postprocess.vlm_client.call_page_structure',side_effect=RuntimeError('fail')):f,route,_,_=run_full([p],m,r/'o',settings(),'F',False)
   self.assertEqual(f[0]['order_repair_status'],'FALLBACK_A1');self.assertTrue(route[0]['fallback_used'])

class RendererTests(unittest.TestCase):
 def render(self,blocks,modify=None):
  td=tempfile.TemporaryDirectory();root=Path(td.name);p=page(1,blocks);f=build_final_page(p)
  if modify:modify(f)
  maps,val=render_goodmd([p],[f],root);return td,root,p,f,maps,val
 def test_text_not_rewritten(self):td,r,_,_,_,_=self.render([b(1,'原文 ABC')]);self.assertIn('原文 ABC',(r/'good.md').read_text(encoding='utf8'));td.cleanup()
 def test_heading_provenance(self):td,r,_,_,m,_=self.render([b(1,'第1章 标题')]);self.assertEqual(m[0]['semantic_type'],'heading');td.cleanup()
 def test_table_provenance(self):td,r,_,_,m,_=self.render([b(1,'A',kind='table',html='<table><tr><td>A</td></tr></table>')]);self.assertTrue(m[0]['source_table_ids']);td.cleanup()
 def test_merge_multi_source(self):
  x=b(1,'标题');x['bbox_norm']=[.2,.2,.5,.22];y=b(2,'18.');y['bbox_norm']=[.1,.2,.18,.22]
  td,r,_,_,m,_=self.render([x,y]);self.assertEqual(len(m[0]['source_block_ids']),2);td.cleanup()
 def test_toc_provenance(self):
  x=b(1,'第1章 .... 5 第2章 .... 9',kind='table',html='<table><tr><td>第1章</td><td>5</td></tr><tr><td>第2章</td><td>9</td></tr></table>');x['parser_label']='document_index'
  td,r,_,_,m,_=self.render([x]);self.assertEqual(m[0]['source_block_ids'][0],'P0001-B001');td.cleanup()
 def test_line_ranges(self):td,r,_,_,m,v=self.render([b(1)]);self.assertTrue(v['source_map_line_ranges_valid']);td.cleanup()
 def test_good_no_page_label(self):td,r,_,_,_,v=self.render([b(1)]);self.assertFalse(v['good_md_contains_physical_page_labels']);td.cleanup()
 def test_debug_has_page_label(self):td,r,_,_,_,v=self.render([b(1)]);self.assertTrue(v['debug_md_contains_physical_page_labels']);td.cleanup()
 def test_render_coverage_100(self):td,r,_,_,_,v=self.render([b(1),b(2)]);self.assertEqual(v['render_source_coverage'],1);td.cleanup()
 def test_duplicate_detection(self):
  def mod(f):f['ordered_elements'].append(dict(f['ordered_elements'][0]))
  td,r,_,_,_,v=self.render([b(1)],mod);self.assertTrue(v['duplicate_source_ids']);td.cleanup()
 def test_chunk_preflight(self):td,r,_,f,_,_=self.render([b(1,'第1章 标题'),b(2,'正文')]);self.assertTrue(chunk_preflight((r/'good.md').read_text(encoding='utf8'),[f])['ready_for_chunker']);td.cleanup()
 def test_no_downstream_artifacts(self):td,r,_,_,_,_=self.render([b(1)]);self.assertFalse((r/'chunks.jsonl').exists());td.cleanup()

class IntegrityReportTests(unittest.TestCase):
 def test_source_coverage_helper(self):p=page(1,[b(1),b(2)]);self.assertEqual(source_text_coverage(p,build_final_page(p))['coverage'],1)
 def test_unknown_reported(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d);p=page(1,[b(1)]);f=build_final_page(p);f.update({'order_repair_status':'NOT_CALLED','table_repair_status':'NOT_CALLED'});f['ordered_elements'][0]['source_ids']=['UNKNOWN'];route=[{'page_no':1,'order_risk':False,'order_risk_reasons':[],'table_risk':False,'table_risk_reasons':[],'vlm_order_called':False,'vlm_order_status':'NOT_CALLED','vlm_table_called':False,'vlm_table_status':'NOT_CALLED','fallback_used':False}]
   report,_,_=build_reports([p],[f],route,[],0,r,settings(),'F');self.assertEqual(report['unknown_source_ids'],['UNKNOWN'])

if __name__=='__main__':unittest.main()
