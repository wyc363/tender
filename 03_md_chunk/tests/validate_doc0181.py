"""Document-specific R1-R8 checks; values here never affect production code."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    chunks = [json.loads(line) for line in (args.output / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
    events = [json.loads(line) for line in (args.output / "parse_events.jsonl").read_text(encoding="utf-8").splitlines()]
    table_one = [c for c in chunks if c.get("table_index") == 1]
    row_ten = [c for c in table_one if c.get("table_row_start") == 10]
    amendments = [c for c in chunks if c.get("special_type") == "AMENDMENT_TABLE"]

    def text_for(section):
        return "\n".join(c["text"] for c in chunks if c["section_heading"] == section)

    heading_positions = {event["heading_text"]: event["line_start"] for event in events if event["event_type"] == "HEADING"}
    r1_five = text_for("5.踏勘现场和标前会议")
    r1_six = text_for("6.知识产权")
    checks = {
        "R1_5_6_NOT_MIXED": all(x in r1_five for x in ("5.1", "5.2", "5.3")) and "6.1" not in r1_five
                              and all(x in r1_six for x in ("6.1", "6.2", "6.3", "6.4", "6.5")),
        "R2_9_10_11_HEADING_ORDER": heading_positions["9.招标文件的修改、补充"] < heading_positions["10.招标文件异议处理"]
                                     < heading_positions["（三）投标文件的编制"] < heading_positions["11.投标文件的语言及度量衡单位"],
        "R3_PREFACE_TABLE_TYPED": bool(table_one) and all(c["chunk_type"] == "TABLE" for c in table_one),
        "R4_ROW_10_CONTEXT": bool(row_ten) and all(all(value in c["text"] for value in ("项号：10", "条款号：3.1", "合格投标人资格条件")) for c in row_ten),
        "R5_AMENDMENT_RECOGNIZED": bool(amendments) and "第1章“招标公告”7.2、7.3条款" in "\n".join(c["text"] for c in amendments)
                                   and "删除此条款要求" in "\n".join(c["text"] for c in amendments),
        "R6_AMENDMENT_7_ROWS": min(c["table_row_start"] for c in amendments) == 1 and max(c["table_row_end"] for c in amendments) == 7,
        "R7_CLAUSE_ORDER_PRESERVED": r1_five.find("5.1") < r1_five.find("5.2") < r1_five.find("5.3"),
        "R8_HEADING_NOT_CLAUSE": all("5" not in c["clause_refs"] for c in chunks if c["section_heading"] == "5.踏勘现场和标前会议"),
    }
    report = {"checks": checks, "passed": sum(checks.values()), "failed": sum(not x for x in checks.values()),
              "status": "PASS" if all(checks.values()) else "FAIL"}
    (args.output / "doc0181_regression_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
