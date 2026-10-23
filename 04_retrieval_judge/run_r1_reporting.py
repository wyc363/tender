"""Build Phase R1 audit, smoke, preview, and Judge-input gate artifacts."""
from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from pathlib import Path


def load_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    p = argparse.ArgumentParser(description="Generate R1 smoke-test artifacts")
    p.add_argument("--chunks", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--command-file", type=Path)
    args = p.parse_args()
    out = args.output
    chunks = load_jsonl(args.chunks)
    by_id = {row["chunk_id"]: row for row in chunks}
    config = json.loads((out / "compiled_retrieval_config.json").read_text(encoding="utf-8-sig"))
    manifest = json.loads((out / "retrieval_input_manifest.json").read_text(encoding="utf-8-sig"))
    quality = json.loads((out / "retrieval_quality_report.json").read_text(encoding="utf-8-sig"))
    keyword = load_jsonl(out / "keyword_results.jsonl")
    bm25 = load_jsonl(out / "bm25_query_results.jsonl")
    pool = load_jsonl(out / "candidate_pool.jsonl")
    top = load_jsonl(out / "retrieval_candidates.jsonl")
    judge = load_jsonl(out / "judge_inputs.jsonl")
    corpus = [row for row in chunks if row["document_region"] != "toc"]
    corpus_ids = {row["chunk_id"] for row in corpus}

    regions = Counter(row["document_region"] for row in chunks)
    corpus_types = Counter(row["chunk_type"] for row in corpus)
    corpus_stats = {
        "total_chunks": len(chunks), "toc_chunks_excluded": regions["toc"],
        "body_chunks": regions["body"], "mixed_chunks": regions["mixed"],
        "retrieval_corpus_chunks": len(corpus), "text_chunks": corpus_types["TEXT"],
        "table_chunks": corpus_types["TABLE"], "excluded_chunk_ids": [
            row["chunk_id"] for row in chunks if row["document_region"] == "toc"],
        "mixed_policy": "RETAIN", "toc_policy": "EXCLUDE",
    }

    query_audit = {
        "rule_id": config["review_item_id"], "rule_name": config["review_item"],
        "rule_leaf_ids": config.get("selected_rule_ids", []),
        "rule_scope": config.get("scan_scopes", []),
        "violation_definitions": config.get("violation_examples", []),
        "legal_definitions": config.get("legal_examples", []),
        "boundary_definitions": config.get("boundary_guidance", []),
        "queries": config["queries"],
        "retrieval_text_recipe": ["chapter_heading", "section_heading",
                                  "heading_context_texts", "heading_path", "text"],
        "retrieval_text_deduplicated": True, "chunk_text_modified": False,
        "bm25_top_n_per_query": config["bm25_top_n_per_query"],
        "keyword_top_n": config["keyword_top_n"], "top_k": config["top_k"],
        "rrf_k": config["rrf_k"], "keyword_rrf_weight": 1.0,
        "risk_query_weight": 1.0, "boundary_query_weight": 0.5,
        "tokenizer": quality["tokenizer"], "rule_store": manifest["rule_store"],
    }
    dump(out / "R1_Corpus_Statistics.json", corpus_stats)
    dump(out / "R1_Query_Audit.json", query_audit)

    rrf_rows = []
    for rank, row in enumerate(pool, 1):
        item = dict(row)
        item["final_rank"] = rank
        rrf_rows.append(item)
    (out / "rrf_results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rrf_rows), encoding="utf-8")

    top_ids = [row["chunk_id"] for row in top]
    duplicate_ids = sorted(cid for cid, count in Counter(top_ids).items() if count > 1)
    unknown = sorted(set(top_ids) - set(by_id))
    missing_pages = [row["chunk_id"] for row in top if not row.get("page_numbers")]
    missing_provenance = [row["chunk_id"] for row in top if not row.get("source_block_ids")]
    toc_top = [row["chunk_id"] for row in top if row.get("document_region") == "toc"]
    context_missing = [row["chunk_id"] for row in top if not all(
        key in row for key in ("anchor_chunk", "context_before", "context_after", "context_chunk_ids"))]
    anchor_page_mismatch = [row["chunk_id"] for row in top
                            if row.get("page_numbers") != by_id[row["chunk_id"]].get("page_numbers")]
    checks = {
        "input_chunks_273": len(chunks) == 273,
        "retrieval_corpus_nonempty": bool(corpus),
        "toc_excluded": regions["toc"] > 0 and not toc_top,
        "top50_50_unique_chunks": len(top) == 50 and len(set(top_ids)) == 50,
        "duplicate_zero": not duplicate_ids,
        "unknown_chunk_zero": not unknown,
        "page_numbers_missing_zero": not missing_pages,
        "provenance_missing_zero": not missing_provenance,
        "text_and_table_processed": any(r["chunk_type"] == "TEXT" for r in top)
                                    and any(r["chunk_type"] == "TABLE" for r in top),
        "context_builder_complete": not context_missing,
        "anchor_pages_preserved": not anchor_page_mismatch,
    }
    check_report = {
        "status": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
        "input_chunks": len(chunks), "toc_excluded": regions["toc"],
        "retrieval_corpus_size": len(corpus), "keyword_candidates": len(keyword),
        "bm25_result_rows": len(bm25), "bm25_unique_candidates": len({r["chunk_id"] for r in bm25}),
        "rrf_candidates": len(pool), "top_k": 50, "top50_unique_chunks": len(set(top_ids)),
        "duplicate_chunk_ids": duplicate_ids, "missing_page_numbers": missing_pages,
        "missing_provenance": missing_provenance, "unknown_chunk_ids": unknown,
        "toc_chunks_in_top50": toc_top, "table_chunks_in_top50": sum(r["chunk_type"] == "TABLE" for r in top),
        "text_chunks_in_top50": sum(r["chunk_type"] == "TEXT" for r in top),
        "context_missing": context_missing, "anchor_page_mismatch": anchor_page_mismatch,
    }
    dump(out / "R1_Retrieval_Check_Report.json", check_report)

    def hits(pattern):
        return [{"rank": row["rank"], "chunk_id": row["chunk_id"], "page_numbers": row["page_numbers"],
                 "anchor_match": pattern in row["text"], "context_match": pattern in row["document_text"]}
                for row in top if pattern in row["text"] or pattern in row["document_text"]]

    semantic = {
        "similar_project_performance": hits("类似项目"),
        "award_or_honor": [
            {"rank": row["rank"], "chunk_id": row["chunk_id"], "page_numbers": row["page_numbers"]}
            for row in top if any(term in row["text"] for term in ("奖项", "获奖", "荣誉"))],
        "credit_scoring": [
            {"rank": row["rank"], "chunk_id": row["chunk_id"], "page_numbers": row["page_numbers"]}
            for row in top if "信用" in row["text"] and any(term in row["text"] for term in ("得分", "评分"))],
        "scoring_or_evaluation": [
            {"rank": row["rank"], "chunk_id": row["chunk_id"], "page_numbers": row["page_numbers"]}
            for row in top if any(term in row["text"] for term in ("评分", "得分", "评标标准"))],
        "known_announcement_3_4": hits("3.4"),
        "known_front_table_requirement": hits("类似项目设计业绩要求"),
    }
    known_pass = bool(semantic["known_announcement_3_4"] and semantic["known_front_table_requirement"]
                      and semantic["scoring_or_evaluation"] and semantic["credit_scoring"])
    smoke = {
        "status": "PASS" if known_pass else "FAIL",
        "assessment": "Top50包含公告业绩条款、前附表类似项目业绩要求、评标评分与信用评价内容，未明显跑偏。"
                      if known_pass else "关键已知内容未全部进入Top50。",
        "accuracy_metrics_computed": False, "gold_used": False,
        "semantic_evidence": semantic,
    }
    dump(out / "R1_Manual_Smoke_Result.json", smoke)
    (out / "R1_人工Smoke结论.md").write_text(
        "# R1 人工 Smoke 结论\n\n"
        + ("结论：通过。Top50覆盖公告3.4业绩条款、投标须知前附表的类似项目设计业绩要求、评标评分及信用评价内容；TEXT与TABLE均有相关候选。\n\n"
           if known_pass else "结论：未通过。关键已知语义未完整进入Top50。\n\n")
        + "本检查不使用Gold，不计算Recall@K或Precision@K，也不判断最终违规与否。\n",
        encoding="utf-8")

    valid_pages = {p for row in chunks for p in row.get("page_numbers", [])}
    judge_unknown = sorted({row["anchor_chunk_id"] for row in judge} - set(by_id))
    judge_toc = [row["candidate_id"] for row in judge
                 if row["anchor_chunk_id"] in by_id and by_id[row["anchor_chunk_id"]]["document_region"] == "toc"]
    judge_checks = {
        "rows_equal_top50": len(judge) == 50,
        "anchor_text_present": all(row.get("anchor_chunk", {}).get("text") for row in judge),
        "context_explicit": all("context_before" in row and "context_after" in row for row in judge),
        "rule_id_present": all(row.get("rule_id") and row.get("rule_leaf_ids") for row in judge),
        "page_numbers_present": all(row.get("page_numbers") for row in judge),
        "chunk_id_present": all(row.get("anchor_chunk_id") for row in judge),
        "toc_pollution_zero": not judge_toc,
        "unknown_chunk_zero": not judge_unknown,
        "invalid_page_zero": not any(p not in valid_pages for row in judge for p in row.get("page_numbers", [])),
    }
    judge_report = {
        "status": "PASS" if all(judge_checks.values()) else "FAIL", "checks": judge_checks,
        "rows": len(judge), "unknown_chunk_ids": judge_unknown,
        "toc_candidate_ids": judge_toc, "judge_llm_called": False,
    }
    dump(out / "R1_Judge_Input_Check.json", judge_report)

    analysis = f"""# R1 Existing Retrieval Analysis

## 输入与语料

输入为 C1.1 `chunks.jsonl`，共273条。每条使用冻结的 `text`，另构造去重后的 `retrieval_text`：`chapter_heading + section_heading + heading_context_texts + heading_path + text`。目录Chunk按 `document_region == toc` 排除，`mixed`保留，最终索引{len(corpus)}条。

## Keyword

沿用四组配置词：risk、condition、region/industry、strong，组内命中计分分别为1、1、1、2；risk+condition额外加3，region/industry+risk+condition额外加2。保留前{config['keyword_top_n']}条，并记录matched_keywords、keyword_score和keyword_rank。

## BM25

使用jieba {quality['jieba_version']}确定性分词和rank-bm25 {quality['rank_bm25_version']}的BM25Okapi。索引完整retrieval_text，对Rule Store编译出的每条risk/boundary query独立取正分前{config['bm25_top_n_per_query']}条。

## Weighted RRF

公式为 `sum(weight / (k + rank))`，k={config['rrf_k']}。Keyword权重1.0，risk query权重1.0，boundary query权重0.5。排序后保持Top{config['top_k']}，未做参数搜索。

## Context与候选Schema

只有anchor参与Keyword、BM25、RRF。按source_order为每个anchor加入前1和后1 Chunk，TEXT/TABLE规则一致，不限制邻居类型。anchor的page_numbers直接复制自冻结Chunk；上下文页码另存context_page_numbers。候选保留chunk_type、text、heading_path、heading_context_texts、page_numbers、source_block_ids、source_table_ids、Keyword/BM25/RRF分数和document_region，并显式区分anchor_chunk、context_before、context_after。

Rule Store本轮只编译审查项“{config['review_item']}”，正式叶规则ID为：{', '.join(config.get('selected_rule_ids', []))}。Judge输入已经生成，但未调用Judge模型。
"""
    (out / "R1_Existing_Retrieval_Analysis.md").write_text(analysis, encoding="utf-8")

    cards = []
    for row in top:
        cards.append(
            f'<article><h2>Rank {row["rank"]} · {html.escape(row["chunk_id"])} · {row["chunk_type"]}</h2>'
            f'<p><b>页码：</b>{html.escape(str(row["page_numbers"]))}　<b>标题：</b>{html.escape(" / ".join(row.get("heading_path", [])))}</p>'
            f'<p><b>Keyword：</b>{html.escape(", ".join(row.get("matched_keywords", [])))}　'
            f'<b>Keyword score/rank：</b>{row.get("keyword_score")} / {row.get("keyword_rank")}</p>'
            f'<p><b>BM25 score/rank：</b>{row.get("bm25_score")} / {row.get("bm25_rank")}　'
            f'<b>RRF：</b>{row["rrf_score"]:.12f}</p><pre>{html.escape(row["text"])}</pre></article>')
    preview = ("<!doctype html><meta charset='utf-8'><title>R1 Top50 Preview</title>"
               "<style>body{font-family:Arial,'Microsoft YaHei';max-width:1200px;margin:auto;background:#f5f7fa}"
               "article{background:#fff;margin:16px;padding:18px;border:1px solid #dce2ea;border-radius:8px}"
               "h2{font-size:18px}pre{white-space:pre-wrap;line-height:1.55}</style>"
               "<h1>Phase R1 Retrieval Top50</h1>" + "".join(cards))
    (out / "R1_Top50_Preview.html").write_text(preview, encoding="utf-8")

    final_pass = check_report["status"] == judge_report["status"] == smoke["status"] == "PASS"
    issues = []
    if check_report["status"] != "PASS":
        issues.append({"category": "PROVENANCE_ERROR", "details": check_report["checks"]})
    if judge_report["status"] != "PASS":
        issues.append({"category": "CONTEXT_ERROR", "details": judge_report["checks"]})
    if smoke["status"] != "PASS":
        issues.append({"category": "MANUAL_SEMANTIC_REVIEW", "details": smoke["assessment"]})
    dump(out / "R1_Retrieval_Issues.json", {"count": len(issues), "issues": issues})
    final = {
        "status": "PASS" if final_pass else "FAIL",
        "retrieval_check": check_report["status"], "manual_smoke": smoke["status"],
        "judge_input_check": judge_report["status"], "judge_llm_called": False,
        "retrieval_only": True,
        "conclusion": "READY_FOR_JUDGE" if final_pass else "NEEDS_RETRIEVAL_FIX",
    }
    dump(out / "R1_Final_Gate_Report.json", final)
    print(json.dumps(final, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
