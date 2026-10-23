import json
from pathlib import Path

from export.readable_exporter import InputPaths, export_readable


def _jl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_export_uses_tree_depth_and_groups_table_rows(tmp_path: Path):
    tree = tmp_path / "tree.json"
    units = tmp_path / "units.jsonl"
    tables = tmp_path / "tables.jsonl"
    tree.write_text(json.dumps({
        "document_id": "D", "sections": [
            {"section_id": "S1", "title": "第一章", "depth": 1, "canonical_position": [0, 10, 0]},
            {"section_id": "S2", "title": "第一节", "depth": 2, "canonical_position": [0, 20, 0]},
        ]
    }, ensure_ascii=False), encoding="utf-8")
    base = {"document_id": "D", "section_id": "S2", "reconstruction_status": "OK"}
    _jl(units, [
        {**base, "unit_id": "U9", "unit_type": "PARAGRAPH_UNIT", "text": "正文", "page_start": 0,
         "provenance": [{"page_idx": 0, "bbox_pdf": [0, 30, 1, 31]}], "source_table_group_ids": []},
        {**base, "unit_id": "U1", "unit_type": "TABLE_ROW_GROUP_UNIT", "text": "A: x | B: y", "page_start": 0,
         "provenance": [{"page_idx": 0, "bbox_pdf": [0, 40, 1, 41]}], "source_table_group_ids": ["TG"],
         "table_context": {"column_headers": ["A", "B"], "cells": {"A": "x", "B": "y"}, "table_title": ""}},
    ])
    _jl(tables, [{"table_group_id": "TG", "page_start": 0, "first_fragment_bbox_pdf": [0, 40, 1, 41]}])
    output = tmp_path / "out.md"
    report = export_readable(InputPaths(tree, units, tables), output)
    text = output.read_text(encoding="utf-8")
    assert "## 第一章" in text and "### 第一节" in text
    assert text.index("正文") < text.index("<table>")
    assert text.count("<table>") == 1
    assert report["rendered_content_unit_count"] == 2


def test_uncertain_table_falls_back_without_guessing(tmp_path: Path):
    tree = tmp_path / "tree.json"
    units = tmp_path / "units.jsonl"
    tables = tmp_path / "tables.jsonl"
    tree.write_text(json.dumps({"document_id": "D", "sections": [{"section_id": "S", "title": "章", "depth": 1, "canonical_position": [0, 0, 0]}]}, ensure_ascii=False), encoding="utf-8")
    _jl(units, [{"unit_id": "U", "document_id": "D", "unit_type": "TABLE_ROW_GROUP_UNIT", "section_id": "S",
                "text": "原始表格文本", "page_start": 0, "provenance": [{"page_idx": 0, "bbox_pdf": [0, 10, 1, 11]}],
                "source_table_group_ids": ["TG"], "table_context": {"column_headers": [], "cells": {}, "table_title": ""},
                "reconstruction_status": "OK"}])
    _jl(tables, [{"table_group_id": "TG", "page_start": 0, "first_fragment_bbox_pdf": [0, 10, 1, 11]}])
    output = tmp_path / "out.md"
    report = export_readable(InputPaths(tree, units, tables), output)
    assert "【表格】" in output.read_text(encoding="utf-8")
    assert report["fallback_table_group_ids"] == ["TG"]
