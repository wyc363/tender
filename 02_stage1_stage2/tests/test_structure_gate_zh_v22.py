import json
from pathlib import Path
def test_all_chinese_gates_pass():
    p=Path(__file__).resolve().parents[1]/"outputs/stage2_v2_2/DOC011/structure_quality_report_v2.json"
    report=json.loads(p.read_text(encoding="utf-8"))
    assert report["status_zh"]=="通过"
    assert len(report["gates"])==10 and all(x["status_zh"]=="通过" for x in report["gates"])
    assert not report["failures"]
