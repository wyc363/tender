import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]/"outputs/stage2_v2_2_1/DOC011"
def data(name):return json.loads((ROOT/name).read_text(encoding="utf-8"))
def test_all_local_heading_canonical_parents_exist():
    tree=data("document_tree_v2_2_1.json");ids={x["section_id"] for x in tree["sections"]}
    assert len(tree["local_headings"])==83
    assert all(x["canonical_parent_section_id"] in ids for x in tree["local_headings"])
    assert all("parent_section_id" not in x for x in tree["local_headings"])
def test_all_depths_match_final_parent_graph():
    tree=data("document_tree_v2_2_1.json");by={x["section_id"]:x for x in tree["sections"]}
    assert all(x["depth"]==1 if not x["parent_id"] else x["depth"]==by[x["parent_id"]]["depth"]+1 for x in tree["sections"])
def test_two_numbering_gaps_are_promoted_as_contract_agreement_siblings():
    tree=data("document_tree_v2_2_1.json");by={x["section_id"]:x for x in tree["sections"]}
    def norm(s):return "".join(s.split())
    targets=[x for x in tree["sections"] if any(t in norm(x["title"]) for t in ["二、工程设计范围、阶段与服务内容","八、词语含义"])]
    assert len(targets)==2
    assert all(x["section_role"]=="SUPPLEMENTARY_SECTION" and "合同协议书" in by[x["parent_id"]]["title"] for x in targets)

