import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]/"outputs/stage2_v2_2/DOC011"
def rows(name):return [json.loads(x) for x in (ROOT/name).read_text(encoding="utf-8").splitlines() if x]
def test_table_boundary_and_known_parents():
    groups=rows("table_groups_v2.jsonl")
    assert not [x for x in groups if x["crosses_structural_section_boundary"]]
    tg8=next(x for x in groups if x["table_group_id"]=="DOC011-TG00008")
    assert "专用合同条款数据表" in tg8["new_parent_title"] and len(tg8["physical_fragment_source_atom_ids"])==3
    tg21=next(x for x in groups if x["table_group_id"]=="DOC011-TG00021")
    assert "通用本文件内容补正表" in tg21["new_parent_title"] and len(tg21["physical_fragment_source_atom_ids"])==6
def test_only_pdf124_requires_image_recovery():
    groups=rows("table_groups_v2.jsonl"); flagged=[x for x in groups if x["requires_image_content_recovery"]]
    assert len(flagged)==1 and flagged[0]["page_start"]==123

