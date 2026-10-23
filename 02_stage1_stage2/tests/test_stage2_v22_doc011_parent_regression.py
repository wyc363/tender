import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]/"outputs/stage2_v2_2/DOC011"
def data(name): return json.loads((ROOT/name).read_text(encoding="utf-8"))
def test_required_parent_relations():
    nodes=data("canonical_outline_v2.json"); by={x["section_id"]:x for x in nodes}
    def norm(s):return "".join((s or "").split())
    def check(child,parent,after=0):
        c=next(x for x in nodes if child in norm(x["title"]) and x["anchor"]["page_idx"]>=after)
        assert parent in norm(by[c["parent_id"]]["title"])
    for c,p in [("（一）总则","一、投标须知"),("1.工程说明","（一）总则"),("6.知识产权","（一）总则"),("（二）招标文件","一、投标须知"),("7.招标文件的组成","（二）招标文件"),("10.招标文件异议处理","（二）招标文件"),("第2节通用合同条款","第2节合同条款"),("第3节设计专用合同条款","第2节合同条款"),("附件三保函、保险的格式","二、投标须知和附件")]:check(c,p)

def test_contract_attachments_are_a_complete_sibling_series():
    nodes=data("canonical_outline_v2.json"); by={x["section_id"]:x for x in nodes}
    attachments=[x for x in nodes if 113<=x["anchor"]["page_idx"]<=118 and x["section_role"]=="SUPPLEMENTARY_ATTACHMENT_SECTION"]
    assert len(attachments)==6
    assert len({x["parent_id"] for x in attachments})==1
    assert "设计专用合同条款" in by[attachments[0]["parent_id"]]["title"]

