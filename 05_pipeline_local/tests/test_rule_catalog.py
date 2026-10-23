from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from review_pipeline.rule_catalog import find_review_point, load_rule_catalog
from tools.compile_rule_catalog import REQUIRED_HEADERS, build_catalog


def _workbook(path: Path, rows: list[list[object]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "审查规则"
    sheet.append(REQUIRED_HEADERS)
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def test_compiled_catalog_contains_authoritative_counts_and_current_rule():
    catalog = load_rule_catalog()
    point = find_review_point(catalog, review_point_id="RP_REGION_INDUSTRY_AWARD")
    assert catalog["review_point_count"] == 31
    assert catalog["leaf_rule_count"] == 138
    assert point["catalog_order"] == 12
    assert point["rule_count"] == 12
    assert point["name"] == "地域/行业业绩奖项及外部荣誉作为招投标加分、中标条件"
    assert [entry["rule_no"] for entry in point["rule_entries"]] == point["rule_ids"]
    assert [entry["violation_term"] for entry in point["rule_entries"]] == point["violation_terms"]


def test_excel_compiler_builds_deterministic_runtime_mapping(tmp_path: Path):
    source = tmp_path / "rules.xlsx"
    overrides = tmp_path / "overrides.json"
    overrides.write_text('{"by_name":{"测试审查点":"RP_TEST"}}', encoding="utf-8")
    _workbook(source, [[
        101, "文件内容审查", "测试审查点", "通用", "低", "测试规则", "全国",
        "*1.测试违规", "*1.测试合法", "测试政策", "2026-01-01", "", "所有", "",
    ]])
    first, report = build_catalog(source, "审查规则", overrides)
    second, _ = build_catalog(source, "审查规则", overrides)
    point = first["review_points"][0]
    assert report["status"] == "PASS"
    assert first == second
    assert point["review_point_id"] == "RP_TEST"
    assert point["catalog_order"] == 1
    assert point["rule_entries"][0]["source_row"] == 2


def test_excel_compiler_rejects_conflicting_fixed_fields(tmp_path: Path):
    source = tmp_path / "rules.xlsx"
    overrides = tmp_path / "missing.json"
    base = ["通用", "高", "同一规则", "全国", "*1.违规", "*1.合法", "政策", "", "", "所有", ""]
    _workbook(source, [
        [101, "类型A", "同一审查点", *base],
        [102, "类型B", "同一审查点", *base],
    ])
    _, report = build_catalog(source, "审查规则", overrides)
    assert report["status"] == "FAIL"
    assert any("审查类型不唯一" in error for error in report["errors"])
