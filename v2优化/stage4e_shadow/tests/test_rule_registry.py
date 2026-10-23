from pathlib import Path

from rp12_stage4e.rule_registry import build_scope_registry


ROOT = Path(r"D:\Users\Desktop\招标文件审查")


def test_registry_has_fixed_three_tiers_and_stable_ids():
    registry = build_scope_registry(ROOT / "招标文件审查规则20261013.xlsx", ROOT / "v2优化/新旧规则映射表.xlsx")
    assert registry["counts"] == {"core": 10, "partial_related": 4, "cross_review_point": 2}
    assert {r["rule_id"] for r in registry["rules"]} == {f"20261013:{x}" for x in (6,7,8,9,19,33,34,35,36,37,38,39,40,41,46,47)}
    assert all(r["policy_verified"] is False for r in registry["rules"])
    assert all(r["production_authorized"] is False for r in registry["rules"])


def test_water_rules_keep_industry_boundary():
    registry = build_scope_registry(ROOT / "招标文件审查规则20261013.xlsx", ROOT / "v2优化/新旧规则映射表.xlsx")
    rules = {r["source_rule_number"]: r for r in registry["rules"]}
    for n in range(35, 41):
        assert "水利" in str(rules[n]["industry"])
