"""DOC0404 QA only; does not alter production chunking behavior."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output
    chunks = [json.loads(line) for line in (output / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
    events = [json.loads(line) for line in (output / "parse_events.jsonl").read_text(encoding="utf-8").splitlines()]
    quality = json.loads((output / "chunk_quality_report.json").read_text(encoding="utf-8"))
    before = json.loads((output / "chunker_code_manifest_before.json").read_text(encoding="utf-8"))
    after = json.loads((output / "chunker_code_manifest_after.json").read_text(encoding="utf-8"))
    first = json.loads((output / "first_run_manifest.json").read_text(encoding="utf-8"))

    def section_text(name):
        return "\n".join(c["text"] for c in chunks if c["section_heading"] == name)

    headings = {e["heading_text"]: e["line_start"] for e in events if e["event_type"] == "HEADING"}
    five, six = section_text("5.踏勘现场和标前会议"), section_text("6.知识产权")
    front = [c for c in chunks if c["section_heading"] == "投标须知前附表（格式）"]
    amendments = [c for c in chunks if c["special_type"] == "AMENDMENT_TABLE"]
    amendment_rows = set()
    for c in amendments:
        amendment_rows.update(range(c["table_row_start"], c["table_row_end"] + 1))
    amendment_index = amendments[0]["table_index"] if amendments else None
    expected_amendment_rows = quality["table_business_row_counts"].get(str(amendment_index), 0)
    manual = {
        "5_6_order": "PASS" if (all(x in five for x in ("5.1", "5.2", "5.3")) and "6.1" not in five
                                   and all(x in six for x in ("6.1", "6.2", "6.3", "6.4", "6.5"))) else "FAIL",
        "9_10_11_order": "PASS" if headings["9.招标文件的修改、补充"] < headings["10.招标文件异议处理"]
                                      < headings["（三）投标文件的编制"] < headings["11.投标文件的语言及度量衡单位"] else "FAIL",
        "front_table": "PASS" if front and any(c["chunk_type"] == "TABLE" for c in front)
                                     and all("<table" not in c["text"].lower() for c in chunks if c["chunk_type"] == "TEXT") else "FAIL",
        "amendment_table": "PASS" if amendments and amendment_rows == set(range(1, expected_amendment_rows + 1)) else "FAIL",
    }
    report = {
        "document_id": "DOC0404", "input_sha256": quality["input_sha256"],
        "code_unchanged": before == after, "input_lines": quality["input_lines"],
        "heading_count": quality["heading_event_count"], "table_count": quality["table_event_count"],
        "event_counts": {"heading": quality["heading_event_count"], "text": quality["text_event_count"], "table": quality["table_event_count"]},
        "chunk_count": quality["total_chunk_count"], "text_chunk_count": quality["text_chunk_count"],
        "table_chunk_count": quality["table_chunk_count"], "max_text_chunk_chars": quality["max_text_chunk_chars"],
        "max_table_chunk_chars": quality["max_table_chunk_chars"],
        "forced_split_count": quality["forced_text_split_count"] + quality["forced_table_cell_split_count"],
        "generic_gates": quality["gates"], "manual_checks": manual,
        "first_run_status": first["strict_status"],
    }
    passed = (report["code_unchanged"] and report["first_run_status"] == "PASS"
              and all(report["generic_gates"].values()) and all(value == "PASS" for value in manual.values()))
    report["zero_patch_generalization"] = "PASS" if passed else "FAIL"
    (output / "generalization_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    selected = [c for c in chunks if c["section_heading"] in {"5.踏勘现场和标前会议", "6.知识产权",
                "9.招标文件的修改、补充", "10.招标文件异议处理", "11.投标文件的语言及度量衡单位",
                "投标须知前附表（格式）", "通用本文件内容补正表"}]
    preview = ["# DOC0404 Generalization Preview", ""]
    for c in selected:
        preview += [f"## {c['chunk_id']} | {c['chunk_type']}", "", f"- lines: {c['source_line_start']}-{c['source_line_end']}",
                    f"- section: {c['section_heading']}", f"- special_type: {c['special_type'] or ''}", "", c["text"], "", "---", ""]
    (output / "DOC0404_generalization_preview.md").write_text("\n".join(preview), encoding="utf-8")
    md = ["# DOC0404 零修改泛化报告", "", f"- 输入 SHA-256：`{report['input_sha256']}`",
          f"- 代码前后哈希一致：{report['code_unchanged']}", f"- 第一次运行：{report['first_run_status']}",
          f"- Event：Heading {quality['heading_event_count']} / Text {quality['text_event_count']} / Table {quality['table_event_count']}",
          f"- Chunk：{quality['total_chunk_count']}（TEXT {quality['text_chunk_count']} / TABLE {quality['table_chunk_count']}）",
          f"- 最大 Chunk：TEXT {quality['max_text_chunk_chars']} / TABLE {quality['max_table_chunk_chars']}",
          f"- Forced split：{report['forced_split_count']}", "", "## QA", ""]
    md += [f"- {key}: {value}" for key, value in manual.items()]
    md += ["", f"ZERO-PATCH GENERALIZATION: **{report['zero_patch_generalization']}**", ""]
    (output / "generalization_report.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
