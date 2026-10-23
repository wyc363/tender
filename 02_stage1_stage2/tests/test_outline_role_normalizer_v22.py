from src.document_processing.hierarchy_v22 import normalize_outline_roles_v2

def row(title): return {"raw_title":title,"outline_role":"TOPIC_SECTION","boundary_strength":"SOFT","role_confidence":.9}
def test_content_like_bookmarks_are_not_sections():
    out=normalize_outline_roles_v2([row("2.签约合同价为：人民币（大写）（¥ 元）."),row("2.支付方式：详见专用合同条款。 附件6："),row("（本项目不包含勘察）")])
    assert [x["outline_role"] for x in out]==["CONTENT_ANCHOR","NONSTRUCTURAL_BOOKMARK","CONTENT_ANCHOR"]
    assert out[2]["semantic_hint"]=="APPLICABILITY_STATEMENT_CANDIDATE"

