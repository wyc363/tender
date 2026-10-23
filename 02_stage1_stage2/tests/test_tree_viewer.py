import hashlib
import json
import re
from pathlib import Path

from tools.tree_viewer.build_tree_viewer import build

ROOT=Path(__file__).resolve().parents[1]
INPUT=ROOT/"outputs/stage2_v2_2_2/DOC011/document_tree_v2_2_2.json"
OUTPUT=ROOT/"outputs/stage2_v2_2_2/DOC011/document_tree_viewer.html"

def embedded(html):
    text=re.search(r'<script id="tree-data" type="application/json">(.*?)</script>',html,re.S).group(1)
    return json.loads(text.replace("<\\/","</"))

def test_builds_doc011_without_modifying_input(tmp_path):
    before=hashlib.sha256(INPUT.read_bytes()).hexdigest();out=tmp_path/"viewer.html"
    result=build(INPUT,out)
    assert out.is_file() and result["section_count"]==178
    assert hashlib.sha256(INPUT.read_bytes()).hexdigest()==before

def test_embedded_tree_preserves_parent_and_children_and_chinese():
    source=json.loads(INPUT.read_text(encoding="utf-8"));data=embedded(OUTPUT.read_text(encoding="utf-8"))
    assert len(data["sections"])==178
    assert [(x["section_id"],x["parent_id"],x["children_ids"]) for x in data["sections"]]==[(x["section_id"],x["parent_id"],x["children_ids"]) for x in source["sections"]]
    assert "二、工程设计范围、阶段与服务内容" in OUTPUT.read_text(encoding="utf-8")

def test_required_interactions_are_embedded_and_local_hidden_by_default():
    html=OUTPUT.read_text(encoding="utf-8")
    for token in ["expand-all","collapse-all","function search()","data-role","breadcrumb","showDetail","show-local","canonical_parent_section_id","pageIdx+1"]:
        assert token in html
    assert "forEach(expanded.add)" not in html
    assert re.search(r'id="show-local"(?![^>]*checked)',html)

def test_viewer_has_no_network_dependencies():
    html=OUTPUT.read_text(encoding="utf-8").lower()
    assert not re.search(r'<script[^>]+src=',html)
    assert not re.search(r'<link[^>]+href=',html)
    assert not re.search(r'\b(fetch|xmlhttprequest|websocket)\s*\(',html)

def test_missing_input_has_clear_error(tmp_path):
    try:build(tmp_path/"missing.json",tmp_path/"out.html")
    except FileNotFoundError as exc:assert "Document Tree JSON不存在" in str(exc)
    else:raise AssertionError("missing input should fail")
