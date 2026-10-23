from document_processing.outline_v2 import normalize_roles
def test_long_clause_is_not_section():
    o=[{"outline_id":"O1","raw_level":4,"raw_title":"5.1.1.1 发包人应当遵守法律和技术标准，不得违反相关规定。","title_normalized":"5.1.1.1发包人应当遵守法律和技术标准,不得违反相关规定。"}]
    assert normalize_roles(o,[{"outline_id":"O1","status":"VALID_BASIC"}])[0]["outline_role"] in {"CLAUSE_ANCHOR","CONTENT_ANCHOR"}
def test_chapter_is_structural():
    o=[{"outline_id":"O1","raw_level":1,"raw_title":"第4章 合同条款","title_normalized":"第4章合同条款"}]; assert normalize_roles(o,[{"outline_id":"O1","status":"VALID_BASIC"}])[0]["outline_role"]=="STRUCTURAL_SECTION"
