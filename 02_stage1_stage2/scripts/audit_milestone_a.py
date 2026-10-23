from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "outputs"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    maps = sorted((OUTPUTS / "section_maps").glob("*_section_map.json"))
    plans = sorted((OUTPUTS / "parse_plans").glob("*_parse_plan.json"))
    assert len(maps) == 19, len(maps)
    assert len(plans) == 19, len(plans)
    map_by_id = {load(path)["doc_id"]: load(path) for path in maps}
    plan_by_id = {load(path)["doc_id"]: load(path) for path in plans}
    assert map_by_id.keys() == plan_by_id.keys()
    for doc_id, section_map in map_by_id.items():
        assert len(section_map["file_sha256"]) == 64
        evaluation = [section for section in section_map["sections"] if section["section_role"] == "EVALUATION"]
        assert len(evaluation) == 1, doc_id
        for section in section_map["sections"]:
            assert section["pdf_page_start"] == section["start_page_idx"] + 1
            assert section["pdf_page_end"] == section["end_page_idx"] + 1
            assert 0 <= section["start_page_idx"] <= section["end_page_idx"] < section_map["page_count"]
        plan = plan_by_id[doc_id]
        assert not plan["missing_primary_sections"], doc_id
        assert not plan["missing_required_sections"], doc_id
        assert "CONTRACT" not in {item["role"] for item in plan["ranges"]}, doc_id
        assert section_map["keyword_page_index"]["purpose"] == "range_expansion_only_not_violation_judgment"
        for item in plan["ranges"]:
            assert item["pdf_page_start"] == item["mineru_start"] + 1
            assert item["pdf_page_end"] == item["mineru_end"] + 1
            assert item["original_start_page_idx"] == item["mineru_start"]

    smoke = {
        "福安职业技术学校三贤校区（施工图设计）（重新招标）.pdf": (51, 75),
        "福州新区滨海水厂及配套管网建设工程（投融资建设一体化）.pdf": (84, 102),
    }
    for file_name, expected in smoke.items():
        section_map = next(item for item in map_by_id.values() if item["file_name"] == file_name)
        evaluation = next(section for section in section_map["sections"] if section["section_role"] == "EVALUATION")
        assert (evaluation["pdf_page_start"], evaluation["pdf_page_end"]) == expected
    doc016 = map_by_id["DOC016"]
    determination = next(section for section in doc016["sections"] if section["section_role"] == "DETERMINATION")
    assert (determination["pdf_page_start"], determination["pdf_page_end"]) == (69, 76)
    doc016_plan = plan_by_id["DOC016"]
    assert any(item["role"] == "DETERMINATION" for item in doc016_plan["ranges"])
    print("PASS: A.1 outputs, offsets, required scopes, contract exclusion, keyword index, 2 smoke regressions, DOC016 determination")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
