import json
from pathlib import Path
def test_three_consistency_metrics_are_zero_and_strict_passes():
    p=Path(__file__).resolve().parents[1]/"outputs/stage2_v2_2_1/DOC011/structure_quality_report_v2_2_1.json"
    r=json.loads(p.read_text(encoding="utf-8"));m=r["metrics"]
    assert m["local_heading_invalid_canonical_parent_count"]==0
    assert m["tree_depth_inconsistency_count"]==0
    assert m["high_confidence_numbering_gap_not_promoted_count"]==0
    assert r["status_zh"]=="通过" and not r["failures"]
    assert all(x["passed"] for x in r["v22_regressions"])
