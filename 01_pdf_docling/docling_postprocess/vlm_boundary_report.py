"""A2.1 20-page regression reports and read-only HTML audit preview."""

from __future__ import annotations

import html
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .vlm_boundary_prompt import SCHEMA_VERSION, active_blocks
from .vlm_boundary_schema import validate_boundary
from .vlm_config import ParserVLMSettings
from .vlm_structure import read_jsonl, write_json


DEEP_NUMBER = re.compile(r"^\s*\d+\.\d+\.\d+")


def suspect_hard(page: dict[str, Any], row: dict[str, Any]) -> list[dict[str, Any]]:
    by_id = {b["block_id"]: b for b in active_blocks(page)}; result = []
    hard_count = sum(e.get("boundary_role") == "hard" for e in row.get("elements", []))
    for element in row.get("elements", []):
        if element.get("boundary_role") != "hard": continue
        block = by_id.get(element.get("block_id"), {}); text = " ".join(str(block.get("text", "")).split())
        reasons = []
        if "：" in text and len(text.split("：", 1)[1].strip()) >= 2: reasons.append("colon_followed_by_specific_content")
        if len(text) >= 80: reasons.append("long_complete_statement")
        if text.rstrip().endswith(("。", "；", ";", ".")): reasons.append("prose_ending_punctuation")
        if DEEP_NUMBER.match(text) and len(text) >= 25: reasons.append("deep_number_with_full_rule")
        if len(row.get("elements", [])) >= 4 and hard_count / len(row["elements"]) >= .5:
            reasons.append("page_hard_ratio_high")
        if reasons:
            result.append({"page_no": row["page_no"], "block_id": element["block_id"],
                "text_preview": text[:240], "boundary_role": "hard", "risk_reason": reasons})
    return result


def _old_order(row: dict[str, Any]) -> list[str]:
    return row.get("reading_order", [])


def build_diff(rows: list[dict[str, Any]], old_rows: list[dict[str, Any]]) -> dict[str, Any]:
    old = {r["page_no"]: r for r in old_rows}; pages = []
    for row in rows:
        before = old.get(row["page_no"], {}); new_roles = {e["block_id"]: e["boundary_role"] for e in row.get("elements", [])}
        old_types = {e["block_id"]: e["type"] for e in before.get("classifications", [])}
        pages.append({"page_no": row["page_no"], "old_status": before.get("status"), "new_status": row["status"],
            "old_truncated": any("INVALID_JSON" in x for x in before.get("validation_errors", [])) and
                             before.get("usage", {}).get("completion_tokens") == 1000,
            "new_truncated": row["status"] == "INVALID_TRUNCATED",
            "order_same": _old_order(before) == [e["block_id"] for e in row.get("elements", [])],
            "old_heading": sorted(k for k,v in old_types.items() if v == "heading"),
            "new_hard": sorted(k for k,v in new_roles.items() if v == "hard"),
            "new_soft": sorted(k for k,v in new_roles.items() if v == "soft"),
            "new_none": sorted(k for k,v in new_roles.items() if v == "none"),
            "old_ignore": sorted(k for k,v in old_types.items() if v == "ignore"),
            "old_ignore_preserved": all(k in new_roles for k,v in old_types.items() if v == "ignore")})
    focus = {str(p): next((x for x in pages if x["page_no"] == p), None)
             for p in (4,5,35,62,71,74,83,99,107,108,127,137,140)}
    return {"schema_version": SCHEMA_VERSION, "pages_compared": len(pages), "pages": pages,
            "focus_summary": focus,
            "note": "Old heading and new hard are different tasks; this is an audit comparison, not label equivalence."}


def build_check(page_records: list[dict[str, Any]], rows: list[dict[str, Any]], pages: list[int],
                output_dir: Path, settings: ParserVLMSettings) -> dict[str, Any]:
    records = {p["page_no"]: p for p in page_records}; errors = {}; tables_missing=[]; table_wrong=[]; covered_returned=[]
    for row in rows:
        page=records[row["page_no"]]; active=active_blocks(page)
        covered={b["block_id"] for b in page.get("blocks",[]) if "COVERED_BY_TABLE" in b.get("flags",[])}
        value={"schema_version":row.get("schema_version"),"page_no":row.get("page_no"),
               "page_type":row.get("page_type"),"elements":row.get("elements")}
        if row["status"] == "SUCCESS":
            found=validate_boundary(value,row["page_no"],active,covered,row.get("finish_reason"))
            if found: errors[str(row["page_no"])]=found
        ids={e.get("block_id") for e in row.get("elements",[])}
        for b in active:
            if b["kind"]=="table" and b["block_id"] not in ids: tables_missing.append(b["block_id"])
        for e in row.get("elements",[]):
            b=next((b for b in active if b["block_id"]==e.get("block_id")),None)
            if b and b["kind"]=="table" and e.get("boundary_role")!="none": table_wrong.append(e["block_id"])
        covered_returned.extend(sorted(covered & ids))
    statuses=Counter(r["status"] for r in rows); called=[r for r in rows if r["api_called"]]
    output_files=[p for p in output_dir.rglob('*') if p.is_file()]
    leaked=any(settings.api_key and settings.api_key in p.read_text(encoding='utf-8',errors='ignore') for p in output_files)
    checks={"pages_requested_20":len(pages)==20,"pages_called_20":len(called)==20,
      "one_unique_result_per_page":len(rows)==20 and len({r['page_no'] for r in rows})==20,
      "technical_failed_zero":statuses['TECHNICAL_FAILED']==0,"truncated_zero":statuses['INVALID_TRUNCATED']==0,
      "success_pages_strictly_valid":not errors,"all_active_blocks_preserved":not tables_missing and all(
        len(r.get('elements',[]))==len(active_blocks(records[r['page_no']])) for r in rows if r['status']=='SUCCESS'),
      "covered_by_table_not_returned":not covered_returned,"all_tables_present":not tables_missing,
      "table_boundary_all_none":not table_wrong,"ignore_absent":all(
        e.get('boundary_role')!='ignore' for r in rows for e in r.get('elements',[])),
      "raw_and_audit_complete":all((output_dir/'raw_responses'/f"page_{p:04d}.json").is_file() and
        (output_dir/'request_audit'/f"page_{p:04d}.json").is_file() for p in pages),
      "api_key_not_leaked":not leaked,"no_downstream_outputs":not any((output_dir/n).exists() for n in
        ('good.md','source_map.jsonl','chunks.jsonl','predictions.jsonl'))}
    return {"phase":"A2.1","status":"PASS" if all(checks.values()) and statuses['INVALID']==0 else "COMPLETE_WITH_ISSUES",
            "schema_version":SCHEMA_VERSION,"prompt_version":SCHEMA_VERSION,"checks":checks,
            "status_counts":dict(statuses),"strict_validation_errors":errors,
            "tables_missing":tables_missing,"table_boundary_wrong":table_wrong,"covered_ids_returned":covered_returned,
            "parser_vlm_config":settings.public_dict()}


def write_preview(path: Path, page_records: list[dict[str, Any]], rows: list[dict[str, Any]], a1_dir: Path) -> None:
    records={p['page_no']:p for p in page_records}; cards=[]
    for row in rows:
        by={b['block_id']:b for b in active_blocks(records[row['page_no']])}
        items=[]
        for index,e in enumerate(row.get('elements',[]),1):
            b=by.get(e['block_id'],{}); role=e['boundary_role']; kind=b.get('kind','?')
            label='TABLE' if kind=='table' else role.upper()
            content=b.get('text','')
            items.append(f"<tr class='{html.escape(role)}'><td>{index}</td><td><code>{html.escape(e['block_id'])}</code></td>"
                         f"<td>{html.escape(kind)}</td><td><b>{label}</b></td><td>{html.escape(str(content))}</td></tr>")
        image=(a1_dir/'page_images'/f"page_{row['page_no']:04d}.jpg").as_uri()
        cards.append(f"<section><h2>PDF 第 {row['page_no']} 页 · {row['status']} · page_type={row.get('page_type')}</h2>"
                     f"<div class='grid'><img src='{image}'><table><thead><tr><th>#</th><th>block_id</th><th>A1 kind</th><th>Role</th><th>原文 / table preview</th></tr></thead><tbody>{''.join(items)}</tbody></table></div></section>")
    path.write_text("<!doctype html><meta charset='utf-8'><title>A2.1 20页回归预览</title><style>"
      "body{font-family:system-ui;margin:24px;background:#f5f6f8;color:#17202a}section{background:white;padding:18px;margin:20px 0;border-radius:10px}"
      ".grid{display:grid;grid-template-columns:minmax(360px,42%) 1fr;gap:18px;align-items:start}img{width:100%;border:1px solid #bbb}"
      "table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid #ccd;padding:7px;vertical-align:top}"
      "tr.hard{background:#ffe1df}tr.soft{background:#fff4cf}tr.none{background:#eef4fa}code{white-space:nowrap}</style>"
      "<h1>A2.1 20页 Chunk Boundary 回归审计预览</h1><p>仅供审计；不删除、不改写任何 active block。</p>"+
      ''.join(cards),encoding='utf-8')


def generate_reports(page_records: list[dict[str, Any]], rows: list[dict[str, Any]], pages: list[int],
                     output_dir: Path, a1_dir: Path, old_full_path: Path,
                     settings: ParserVLMSettings) -> dict[str, Any]:
    records={p['page_no']:p for p in page_records}; roles=Counter(); page_types=Counter(); statuses=Counter(r['status'] for r in rows)
    suspects=[]
    for row in rows:
        page_types[row.get('page_type') or 'unavailable']+=1
        for e in row.get('elements',[]): roles[e.get('boundary_role')]+=1
        if row['status']=='SUCCESS': suspects.extend(suspect_hard(records[row['page_no']],row))
    stats={"schema_version":SCHEMA_VERSION,"pages_requested":len(pages),"success":statuses['SUCCESS'],
      "invalid":statuses['INVALID'],"invalid_truncated":statuses['INVALID_TRUNCATED'],
      "technical_failed":statuses['TECHNICAL_FAILED'],"total_active_blocks":sum(len(active_blocks(records[p])) for p in pages),
      "hard":roles['hard'],"soft":roles['soft'],"none":roles['none'],"page_type_counts":dict(page_types),
      "finish_reason_counts":dict(Counter(r.get('finish_reason') for r in rows if r.get('finish_reason'))),
      "suspect_hard_boundary":len(suspects)}
    diff=build_diff(rows,read_jsonl(old_full_path)); check=build_check(page_records,rows,pages,output_dir,settings)
    write_json(output_dir/'A2_1结构统计.json',stats)
    write_json(output_dir/'A2_1_vs_A2Full_20pages.json',diff)
    write_json(output_dir/'SUSPECT_HARD_BOUNDARY.json',{"count":len(suspects),"candidates":suspects})
    write_json(output_dir/'a2_1_check_report.json',check)
    write_preview(output_dir/'A2_1_20页回归可读预览.html',page_records,rows,a1_dir)
    return {"statistics":stats,"diff":diff,"check":check,"suspects":suspects}
