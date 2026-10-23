import unittest

from md_chunking.chunk_provenance import enrich_chunks, overlaps


class ChunkProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.good = "# 第1章 示例\n\n正文甲\n\n## 第一节\n\n正文乙\n\n<table>\n<tr><td>x</td></tr>\n</table>\n"
        self.maps = [
            {"render_id":"R1","semantic_type":"heading","source_block_ids":["H1"],"page_numbers":[1],"md_line_start":1,"md_line_end":1},
            {"render_id":"R2","semantic_type":"text","source_block_ids":["B1"],"page_numbers":[1],"md_line_start":3,"md_line_end":3},
            {"render_id":"R3","semantic_type":"heading","source_block_ids":["H2"],"page_numbers":[2],"md_line_start":5,"md_line_end":5},
            {"render_id":"R4","semantic_type":"text","source_block_ids":["B2"],"page_numbers":[2],"md_line_start":7,"md_line_end":7},
            {"render_id":"R5","semantic_type":"table","source_block_ids":["T1"],"source_table_ids":["T1"],"page_numbers":[2],"md_line_start":9,"md_line_end":11},
        ]

    def test_closed_interval_overlap(self):self.assertTrue(overlaps(3,4,self.maps[1]))
    def test_non_overlap(self):self.assertFalse(overlaps(4,4,self.maps[1]))
    def test_content_mapping_uses_line_range(self):
        c={"source_line_start":3,"source_line_end":3,"chapter_heading":"第1章 示例","section_heading":"第1章 示例"}
        r=enrich_chunks([c],self.maps,self.good,{1:'content',2:'content'})[0];self.assertEqual(r['source_block_ids'],['B1'])
    def test_heading_context_is_separate(self):
        c={"source_line_start":7,"source_line_end":7,"chapter_heading":"第1章 示例","section_heading":"第一节"}
        r=enrich_chunks([c],self.maps,self.good,{1:'content',2:'content'})[0];self.assertEqual(r['heading_source_block_ids'],['H1','H2'])
    def test_table_provenance(self):
        c={"source_line_start":9,"source_line_end":11,"chapter_heading":"第1章 示例","section_heading":"第一节","chunk_type":"TABLE"}
        r=enrich_chunks([c],self.maps,self.good,{1:'content',2:'content'})[0];self.assertEqual(r['source_table_ids'],['T1'])
    def test_cross_page_numbers_preserve_order(self):
        c={"source_line_start":3,"source_line_end":7,"chapter_heading":"第1章 示例","section_heading":"第一节"}
        r=enrich_chunks([c],self.maps,self.good,{1:'content',2:'content'})[0];self.assertEqual(r['page_numbers'],[1,2])
    def test_toc_region(self):
        c={"source_line_start":3,"source_line_end":3};r=enrich_chunks([c],self.maps,self.good,{1:'toc'})[0];self.assertEqual(r['document_region'],'toc')
    def test_mixed_region(self):
        c={"source_line_start":3,"source_line_end":7};r=enrich_chunks([c],self.maps,self.good,{1:'toc',2:'content'})[0];self.assertEqual(r['document_region'],'mixed')
    def test_deterministic(self):
        c={"source_line_start":3,"source_line_end":3};a=enrich_chunks([c],self.maps,self.good,{1:'content'});b=enrich_chunks([c],self.maps,self.good,{1:'content'});self.assertEqual(a,b)

if __name__=='__main__':unittest.main()
