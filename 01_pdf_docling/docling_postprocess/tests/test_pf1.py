import json,tempfile,unittest
from pathlib import Path
from docling_postprocess.deterministic_structure import heading_decision,detect_toc_page,build_final_page,source_text_coverage
from docling_postprocess.structure_risk import detect_order_risk,detect_table_risk
from docling_postprocess.vlm_repair_schema import order_schema,table_schema,validate_order,validate_table,parse
from docling_postprocess.vlm_repair_router import run_pf1
from docling_postprocess.vlm_client import VLMClientResult
from docling_postprocess.vlm_config import ParserVLMSettings

def block(i,text,kind='text',label='text',bbox=None,flags=None,html=''):
 return {'block_id':f'P0001-B{i:03d}','kind':kind,'text':text,'parser_label':label,'bbox_norm':bbox or [.1,.1+i*.02,.8,.115+i*.02],'flags':flags or [],'table_html':html}
def decision(text,**kw):return heading_decision(block(1,text,**kw))['is_heading']
def settings():return ParserVLMSettings('https://x.cn-beijing.example/v1','secret','qwen3.7-flash',0,2500,False,120,1,1572864,1)

class HeadingTests(unittest.TestCase):
 def test_chapter(self):self.assertTrue(decision('第 1 章 招标公告'))
 def test_section(self):self.assertTrue(decision('第2节 投标须知'))
 def test_part(self):self.assertTrue(decision('第一部分 招标文件'))
 def test_cn_top(self):self.assertTrue(decision('一、投标须知'))
 def test_cn_paren(self):self.assertTrue(decision('（一）总则'))
 def test_arabic_one(self):self.assertTrue(decision('1.招标条件'))
 def test_arabic_two(self):self.assertTrue(decision('2.项目概况'))
 def test_deep_21(self):self.assertFalse(decision('2.1 项目名称',label='section_header'))
 def test_deep_long(self):self.assertFalse(decision('2.3 '+('具体建设要求'*20),label='section_header'))
 def test_deep_1031(self):self.assertFalse(decision('10.3.1 定金比例',label='section_header'))
 def test_colon_value(self):self.assertFalse(decision('项目名称：福安学校',label='section_header'))
 def test_long_body(self):self.assertFalse(decision('这是一段正文要求'*20,label='section_header'))
 def test_parser_label_weak(self):
  plain=heading_decision(block(1,'普通文本',label='text'));header=heading_decision(block(1,'普通文本',label='section_header'))
  self.assertEqual(header['score'],plain['score']+1)
 def test_note_protected(self):self.assertFalse(decision('说明：',label='section_header'))

class TocTests(unittest.TestCase):
 def page(self):return {'page_no':1,'blocks':[block(1,'第1章 .... 5 第2章 .... 9',kind='table',label='document_index',html='<table><tr><td>第1章</td><td>5</td></tr><tr><td>第2章</td><td>9</td></tr></table>')]}
 def test_toc_detected(self):self.assertTrue(detect_toc_page(self.page()))
 def test_toc_not_body(self):self.assertFalse(detect_toc_page({'page_no':1,'blocks':[block(1,'普通正文')]}))
 def test_toc_source_unchanged(self):p=self.page();before=json.dumps(p,ensure_ascii=False);detect_toc_page(p);self.assertEqual(before,json.dumps(p,ensure_ascii=False))

class OrderTests(unittest.TestCase):
 def test_normal_no_risk(self):self.assertFalse(detect_order_risk({'blocks':[block(1,'正文'),block(2,'正文') ]})['risk'])
 def test_isolated_number(self):self.assertTrue(detect_order_risk({'blocks':[block(1,'18.'),block(2,'标题')]})['risk'])
 def test_isolated_number_code_only(self):self.assertFalse(detect_order_risk({'blocks':[block(1,'18.'),block(2,'标题')]})['needs_vlm'])
 def test_multicolumn(self):
  bs=[block(1,'左栏长正文'*8,bbox=[.1,.1,.4,.2]),block(2,'右栏长正文'*8,bbox=[.6,.1,.9,.2])]*4
  for i,b in enumerate(bs):b['block_id']=f'X{i}'
  self.assertTrue(detect_order_risk({'blocks':bs})['needs_vlm'])
 def test_order_valid(self):self.assertEqual(validate_order({'schema_version':'PF1-order-v1','page_no':1,'ordered_block_ids':['A','B']},1,['A','B']),[])
 def test_order_duplicate(self):self.assertIn('ORDER_DUPLICATE',validate_order({'schema_version':'PF1-order-v1','page_no':1,'ordered_block_ids':['A','A']},1,['A','B']))
 def test_order_unknown(self):self.assertIn('ORDER_NOT_EXACT_ACTIVE_SET',validate_order({'schema_version':'PF1-order-v1','page_no':1,'ordered_block_ids':['A','X']},1,['A','B']))
 def test_merge_lossless(self):
  p={'page_no':1,'blocks':[block(1,'标题',label='section_header',bbox=[.2,.2,.5,.22]),block(2,'18.',label='section_header',bbox=[.1,.2,.18,.22])]};f=build_final_page(p)
  self.assertEqual(f['ordered_elements'][0]['source_ids'],['P0001-B002','P0001-B001'])

class TableTests(unittest.TestCase):
 def test_normal_table(self):self.assertFalse(detect_table_risk({'blocks':[block(1,'A',kind='table',html='<table><tr><td>A</td></tr></table>')]})['risk'])
 def test_empty_table(self):self.assertTrue(detect_table_risk({'blocks':[block(1,'A',kind='table',html='')]})['risk'])
 def test_interleaving(self):
  bs=[block(i,'T',kind='table',bbox=[.1,.1*i,.9,.1*i+.04],html='<table><tr><td>A</td></tr></table>') for i in range(1,4)]
  bs += [block(i+3,'orphan',bbox=[.1,.12+i*.03,.8,.14+i*.03]) for i in range(5)]
  self.assertTrue(detect_table_risk({'blocks':bs})['risk'])
 def test_table_ids_only(self):self.assertEqual(validate_table({'schema_version':'PF1-table-v1','page_no':1,'tables':[{'source_ids':['A'],'rows':['A']}]},1,['A']),[])
 def test_table_free_text_rejected(self):self.assertTrue(validate_table({'schema_version':'PF1-table-v1','page_no':1,'tables':[{'source_ids':['A'],'rows':['A'],'text':'invented'}]},1,['A']))
 def test_table_unknown_rejected(self):self.assertTrue(validate_table({'schema_version':'PF1-table-v1','page_no':1,'tables':[{'source_ids':['X'],'rows':['X']}]},1,['A']))

class IntegrityTests(unittest.TestCase):
 def test_all_active_preserved(self):
  p={'page_no':1,'blocks':[block(1,'a'),block(2,'b')]};self.assertEqual(source_text_coverage(p,build_final_page(p))['coverage'],1)
 def test_covered_excluded(self):
  p={'page_no':1,'blocks':[block(1,'a'),block(2,'b',flags=['COVERED_BY_TABLE'])]};self.assertEqual(source_text_coverage(p,build_final_page(p))['expected'],1)
 def test_public_key_hidden(self):self.assertEqual(settings().public_dict()['api_key'],'CONFIGURED')
 def test_json_parse(self):self.assertEqual(parse('{"a":1}')[1],[])
 def test_check_only_zero_api(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);img=root/'a.jpg';img.write_bytes(b'x');p={'page_no':1,'blocks':[block(1,'正文')]};m={'images':[{'page_no':1,'path':str(img)}]};calls=[]
   _,_,u,s=run_pf1([p],m,root/'o',settings(),[1],False,lambda *a,**k:calls.append(1))
   self.assertEqual((calls,u['api_calls'],s['source_text_coverage_100_percent']),([],0,True))
 def test_failure_falls_back_a1(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);img=root/'a.jpg';img.write_bytes(b'x');bs=[block(1,'一、标题'),block(2,'/。'),block(3,'二、标题'),block(4,'/。')];p={'page_no':1,'blocks':bs};m={'images':[{'page_no':1,'path':str(img)}]}
   def fail(*a,**k):raise RuntimeError('offline')
   f,r,u,_=run_pf1([p],m,root/'o',settings(),[1],True,fail)
   self.assertEqual([e['source_ids'][0] for e in f[0]['ordered_elements']],[b['block_id'] for b in bs]);self.assertTrue(r[0]['fallback_to_a1_order'])

if __name__=='__main__':unittest.main()
