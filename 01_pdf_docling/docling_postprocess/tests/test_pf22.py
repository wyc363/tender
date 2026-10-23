import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docling_postprocess.deterministic_structure import build_final_page, source_text_coverage
from docling_postprocess.goodmd_renderer import render_goodmd
from docling_postprocess.pf2_pipeline import run_full
from docling_postprocess.table_repair_scope import build_table_repair_scope, validate_table_repair_scope
from docling_postprocess.vlm_config import ParserVLMSettings
from docling_postprocess.vlm_repair_schema import table_schema

def b(i, text='text', kind='text', box=None, label='text', html='<table><tr><td>x</td></tr></table>', flags=None):
    prefix='T' if kind=='table' else 'B'
    return {'block_id':f'P0001-{prefix}{i:03d}','kind':kind,'text':text,'parser_label':label,
            'bbox_norm':box or [.1,.1,.9,.2],'flags':flags or [],'table_html':html if kind=='table' else ''}

def page(blocks,n=1):
    for x in blocks:x['block_id']=f'P{n:04d}-'+x['block_id'].split('-')[-1]
    return {'page_no':n,'blocks':blocks}

def settings():return ParserVLMSettings('https://x.example/v1','secret','qwen3.7-flash',0,2500,False,120,1,1572864,1)

def page142():
    blocks=[b(1,kind='table',box=[.131945,.084978,.824974,.230855],html='')]
    boxes=[[.654492,.245345,.890294,.259593],[.133669,.279207,.18534,.293454]]
    boxes += [[.13,.30+i*.05,.88,.34+i*.05] for i in range(10)]
    blocks += [b(i+1,f'body {i+1}',box=box) for i,box in enumerate(boxes)]
    return page(blocks,142)

def page74():
    blocks=[b(1,'lead heading',box=[.16,.09,.45,.11],label='section_header'),b(2,'lead body',box=[.13,.12,.88,.20]),b(3,'table title',box=[.44,.21,.62,.23],label='section_header')]
    blocks += [b(1,kind='table',box=[.12,.243,.89,.306]),b(2,kind='table',box=[.12,.342,.89,.392])]
    blocks += [b(i,f'row {i}',box=[.13,.400+(i-4)*.02,.87,.415+(i-4)*.02]) for i in range(4,10)]
    blocks += [b(3,kind='table',box=[.19,.552,.89,.599])]
    blocks += [b(i,f'row {i}',box=[.13,.609+(i-10)*.03,.87,.625+(i-10)*.03]) for i in range(10,15)]
    blocks += [b(4,kind='table',box=[.18,.779,.89,.828])]
    blocks += [b(i,f'row {i}',box=[.13,.837+(i-15)*.019,.49,.849+(i-15)*.019]) for i in range(15,19)]
    return page(blocks,74)

class ScopeGeometryTests(unittest.TestCase):
    def test_single_table_overlap_text_eligible(self):
        p=page([b(1,kind='table',box=[.1,.1,.9,.4],html=''),b(1,'cell',box=[.2,.2,.4,.3])]);s=build_table_repair_scope(p)
        self.assertIn('P0001-B001',s['table_groups'][0]['eligible_source_ids'])
    def test_single_table_below_body_not_eligible(self):
        p=page142();s=build_table_repair_scope(p);self.assertNotIn('P0142-B001',s['table_groups'][0]['eligible_source_ids'])
    def test_single_table_adjacency_is_not_enough(self):
        p=page([b(1,kind='table',box=[.1,.1,.9,.2],html=''),b(1,'near',box=[.1,.201,.9,.22])]);s=build_table_repair_scope(p)
        self.assertEqual(s['table_groups'][0]['eligible_source_ids'],['P0001-T001'])
    def test_page142_all_body_excluded(self):
        s=build_table_repair_scope(page142());self.assertTrue(all(f'P0142-B{i:03d}' in s['excluded_source_ids'] for i in range(1,13)))
    def test_page142_unrepairable(self):self.assertEqual(build_table_repair_scope(page142())['scope_status'],'UNREPAIRABLE_TABLE_RISK')
    def test_multiple_aligned_tables_form_group(self):self.assertEqual(len(build_table_repair_scope(page74())['table_groups']),1)
    def test_multi_table_interstitial_text_eligible(self):self.assertIn('P0074-B004',build_table_repair_scope(page74())['table_groups'][0]['eligible_source_ids'])
    def test_multi_table_scope_preserves_page_order(self):
        ids=build_table_repair_scope(page74())['table_groups'][0]['eligible_source_ids']
        self.assertLess(ids.index('P0074-B004'),ids.index('P0074-T003'))
    def test_multi_table_short_trailing_text_eligible(self):self.assertIn('P0074-B018',build_table_repair_scope(page74())['table_groups'][0]['eligible_source_ids'])
    def test_multi_table_leading_text_excluded(self):
        s=build_table_repair_scope(page74());self.assertTrue(all(x in s['excluded_source_ids'] for x in ['P0074-B001','P0074-B002','P0074-B003']))
    def test_insufficient_x_overlap_does_not_merge(self):
        p=page([b(1,kind='table',box=[.05,.1,.4,.2],html=''),b(2,kind='table',box=[.6,.3,.95,.4],html=''),b(3,kind='table',box=[.05,.5,.4,.6],html='')])
        self.assertEqual(len(build_table_repair_scope(p)['table_groups']),3)
    def test_heading_boundary_stops_group_merge(self):
        p=page([b(1,kind='table',box=[.1,.1,.9,.2],html=''),b(1,'第1章 标题',box=[.2,.22,.7,.25]),b(2,kind='table',box=[.1,.3,.9,.4],html=''),b(3,kind='table',box=[.1,.5,.9,.6],html='')])
        self.assertEqual(len(build_table_repair_scope(p)['table_groups']),2)
    def test_heading_boundary_stops_trailing(self):
        p=page([b(1,kind='table',box=[.1,.1,.9,.2],html=''),b(2,kind='table',box=[.1,.3,.9,.4],html=''),b(3,kind='table',box=[.1,.5,.9,.6],html=''),b(1,'第1章 标题',box=[.1,.61,.9,.63])])
        self.assertNotIn('P0001-B001',build_table_repair_scope(p)['table_groups'][0]['eligible_source_ids'])

class SchemaValidatorTests(unittest.TestCase):
    def test_dynamic_schema_only_has_eligible_enum(self):
        ids=['T1','B1'];schema=table_schema(ids);self.assertEqual(schema['properties']['tables']['items']['properties']['source_ids']['items']['enum'],ids)
    def test_out_of_scope_id_fails(self):
        p=page142();s=build_table_repair_scope(p);v={'tables':[{'source_ids':['P0142-T001','P0142-B001'],'rows':['P0142-T001','P0142-B001']}]}
        errors,_=validate_table_repair_scope(v,p,s);self.assertIn('TABLE_0_OUT_OF_SCOPE_ID',errors)
    def test_repair_without_table_source_fails(self):
        p=page([b(1,kind='table',box=[.1,.1,.9,.4],html=''),b(1,'cell',box=[.2,.2,.4,.3])]);s=build_table_repair_scope(p)
        errors,_=validate_table_repair_scope({'tables':[{'source_ids':['P0001-B001'],'rows':['P0001-B001']}]},p,s);self.assertIn('TABLE_0_MISSING_TABLE_SOURCE',errors)
    def test_invalid_repair_is_not_clipped(self):
        p=page142();s=build_table_repair_scope(p);returned=['P0142-T001','P0142-B001'];_,rows=validate_table_repair_scope({'tables':[{'source_ids':returned,'rows':returned}]},p,s)
        self.assertEqual(rows[0]['vlm_returned_source_ids'],returned);self.assertEqual(rows[0]['validation_status'],'INVALID_REPAIR_SCOPE')
    def test_source_cannot_be_reused(self):
        p=page74();s=build_table_repair_scope(p);ids=s['table_groups'][0]['eligible_source_ids']
        errors,_=validate_table_repair_scope({'tables':[{'source_ids':ids,'rows':ids},{'source_ids':ids,'rows':ids}]},p,s);self.assertIn('TABLE_REPAIR_SOURCE_REUSED',errors)

class IntegrationSafetyTests(unittest.TestCase):
    def test_renderer_consumes_only_valid_repair(self):
        p=page([b(1,kind='table'),b(1,'body',box=[.1,.21,.9,.3])]);f=build_final_page(p);f['table_repairs']=[{'source_ids':['P0001-T001','P0001-B001'],'rows':['P0001-T001','P0001-B001'],'validation_status':'VALID'}]
        with tempfile.TemporaryDirectory() as d:maps,_=render_goodmd([p],[f],Path(d));self.assertEqual(len(maps),1)
    def test_invalid_repair_cannot_delete_text(self):
        p=page([b(1,kind='table'),b(1,'body',box=[.1,.21,.9,.3])]);f=build_final_page(p);f['table_repairs']=[{'source_ids':['P0001-T001','P0001-B001'],'rows':['P0001-T001','P0001-B001'],'validation_status':'INVALID_REPAIR_SCOPE'}]
        with tempfile.TemporaryDirectory() as d:maps,_=render_goodmd([p],[f],Path(d));self.assertEqual(len(maps),2);self.assertIn('body',(Path(d)/'good.md').read_text())
    def test_page17_normal_table_no_api(self):self._normal_no_api(17)
    def test_page167_normal_table_no_api(self):self._normal_no_api(167)
    def _normal_no_api(self,n):
        p=page([b(1,kind='table',box=[.1,.1,.9,.9])],n)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);img=root/'x.jpg';img.write_bytes(b'x');manifest={'images':[{'page_no':n,'path':str(img)}]}
            with patch('docling_postprocess.vlm_client.call_page_structure') as api: finals,routes,calls,_=run_full([p],manifest,root/'out',settings(),'F',False)
            api.assert_not_called();self.assertFalse(routes[0]['vlm_table_called']);self.assertEqual(calls,[])
    def test_page142_skips_api_and_keeps_coverage(self):
        p=page142()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);img=root/'x.jpg';img.write_bytes(b'x');manifest={'images':[{'page_no':142,'path':str(img)}]}
            with patch('docling_postprocess.vlm_client.call_page_structure') as api: finals,routes,calls,_=run_full([p],manifest,root/'out',settings(),'F',False)
            api.assert_not_called();self.assertEqual(finals[0]['table_repair_status'],'SKIPPED_NO_ELIGIBLE_SOURCE_FACTS');self.assertEqual(source_text_coverage(p,finals[0])['coverage'],1)
    def test_check_only_scope_has_no_api_path(self):
        with patch('docling_postprocess.vlm_client.call_page_structure') as api:build_table_repair_scope(page74());api.assert_not_called()

if __name__=='__main__':unittest.main()
