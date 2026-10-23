from src.document_processing.table_text_consumption_v32 import full_cells,span_overlay
from src.document_processing.front_matter_v32 import build_front_matter

def test_continuation_is_consumed_into_explicit_cell():
    u={"text":"编列内容: 前半\n编列内容: 后半","table_context":{"continuation":True,"column_headers":["编列内容"],"cells":{"编列内容":"前半"}}}
    assert full_cells(u)["编列内容"]=="前半 后半"

def test_colspan_overlay_does_not_copy_long_text():
    cells={"A":"x"*20,"B":"x"*20,"C":"x"*20}
    assert span_overlay(cells,["A","B","C"])==[{"value":"x"*20,"column_start":0,"colspan":3}]

def test_front_matter_is_sidecar():
    blocks=[{"canonical_block_id":"B","front_matter_role":"PREFACE","page_idx":1,"bbox_pdf":[0,1,2,3],"text_canonical":"使用说明"}]
    result=build_front_matter(blocks,(4,0,0))
    assert next(x for x in result if x["front_matter_type"]=="DOCUMENT_INSTRUCTION")["effect_context"] is True
