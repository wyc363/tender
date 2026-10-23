from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .utils import read_json, read_jsonl


def _h(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = "、".join(str(item) for item in value)
    return html.escape(str(value))


def _text(value: Any, empty: str = "无") -> str:
    value = value if value not in (None, "", []) else empty
    return f'<div class="prose">{_h(value)}</div>'


def _status_class(status: str) -> str:
    return {"PASS": "pass", "FAIL": "fail", "REVIEW": "review"}.get(status, "unknown")


def _pages(row: dict[str, Any], candidate: dict[str, Any]) -> list[Any]:
    return row.get("anchor_page_numbers") or candidate.get("page_numbers") or []


def _client_result_section(
    content: dict[str, Any],
    document_result: str,
    usage: dict[str, Any],
) -> str:
    """Render the final client-contract JSON without re-deriving its fields."""
    items = content.get("result") or []
    rendered: list[str] = []
    for item in items:
        terms = item.get("term") or []
        term_html = "".join(f"<li>{_h(term)}</li>" for term in terms) or "<li>无</li>"
        texts = item.get("text") or []
        pages = item.get("page") or []
        evidence_html = []
        for index, source_text in enumerate(texts):
            # Top-50 interface output has one original chunk per result item;
            # a chunk may span more than one physical PDF page.
            page = pages if len(texts) == 1 else (pages[index] if index < len(pages) else None)
            evidence_html.append(
                f'<section class="client-evidence"><h4>相关原文 {index + 1} · PDF页码：{_h(page) or "无"}</h4>'
                f'{_text(source_text)}</section>'
            )
        if not evidence_html:
            evidence_html.append('<div class="empty">无相关原文</div>')
        status = "PASS" if item.get("is_pass") else "FAIL"
        css = _status_class(status)
        rendered.append(f"""
        <article class="client-item {css}">
          <div class="candidate-head"><div><span class="rank">#{_h(item.get('index'))}</span>
            <span class="badge {css}">{_h(status)}</span><strong>{_h(item.get('name'))}</strong></div></div>
          <div class="meta-grid client-meta">
            <div><b>审查类型</b><span>{_h(item.get('type')) or '无'}</span></div>
            <div><b>风险等级</b><span>{_h(item.get('risk_level')) or '无'}</span></div>
            <div><b>is_pass</b><span>{_h(str(bool(item.get('is_pass'))).lower())}</span></div>
            <div><b>扫描范围</b><span>{_h(content.get('scope')) or '无'}</span></div>
          </div>
          <div class="client-body">
            <section><h3>审查规则说明</h3>{_text(item.get('rule'))}</section>
            <section><h3>政策依据</h3>{_text(item.get('policy'))}</section>
            <section><h3>违规条目</h3><ul class="terms">{term_html}</ul></section>
            <section><h3>审查结论说明</h3>{_text(item.get('reason'))}</section>
            <section><h3>相关原文与页码</h3>{''.join(evidence_html)}</section>
            <section><h3>修改建议</h3>{_text(content.get('suggestions'))}</section>
          </div>
        </article>""")
    usage_line = (
        f"输入 {_h(usage.get('prompt_tokens', 0))} · 输出 {_h(usage.get('completion_tokens', 0))} · "
        f"合计 {_h(usage.get('total_tokens', 0))} tokens"
    )
    return f"""
    <section class="client-result-panel">
      <div class="section-title"><div><h2>甲方接口审查结果</h2>
        <p>以下内容直接读取 client_content_result.json；内部 REVIEW 状态单独显示，is_pass 保持接口布尔值。</p></div>
        <span class="usage">{usage_line}</span></div>
      {''.join(rendered) if rendered else '<div class="empty">未生成接口结果</div>'}
    </section>"""


def build_combined_report(document_root: Path, output: Path | None = None) -> Path:
    """Merge Top-K retrieval evidence and validated Judge results into one offline HTML."""
    retrieval_dir = document_root / "04_retrieval"
    judge_dir = document_root / "05_judge"
    candidates = read_jsonl(retrieval_dir / "candidate_top50.jsonl")
    predictions = {row.get("candidate_id"): row for row in read_jsonl(judge_dir / "predictions.jsonl")}
    validated = {row.get("candidate_id"): row for row in read_jsonl(judge_dir / "validated_predictions.jsonl")}
    summary = read_json(document_root / "pipeline_summary.json") if (document_root / "pipeline_summary.json").is_file() else {}
    final = read_json(judge_dir / "final_review_result.json")
    client_content_path = document_root / "client_content_result.json"
    client_response_path = document_root / "client_api_response.json"
    client_content = read_json(client_content_path) if client_content_path.is_file() else {}
    client_response = read_json(client_response_path) if client_response_path.is_file() else {}
    judge_inputs = read_jsonl(retrieval_dir / "judge_inputs.jsonl")
    rule_context = judge_inputs[0].get("rule_context", "") if judge_inputs else ""

    rows: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    missing: list[str] = []
    for candidate in sorted(candidates, key=lambda x: x.get("rank", 10**9)):
        cid = candidate.get("candidate_id")
        checked = validated.get(cid)
        if checked is None:
            missing.append(str(cid))
            checked = {"validated_result": "UNKNOWN", "reason": "缺少验证结果"}
        rows.append((candidate, predictions.get(cid, {}), checked))

    counts = Counter(str(row[2].get("validated_result", "UNKNOWN")) for row in rows)
    cards: list[str] = []
    for candidate, raw, checked in rows:
        status = str(checked.get("validated_result", "UNKNOWN"))
        css = _status_class(status)
        rank = candidate.get("rank") or checked.get("retrieval_rank")
        anchor = candidate.get("anchor_chunk") or {}
        anchor_text = candidate.get("text") or anchor.get("text") or ""
        reviewed_text = raw.get("片段原文") or candidate.get("document_text") or anchor_text
        heading = candidate.get("heading_path") or anchor.get("heading_path") or []
        evidence = (checked.get("violation_original_text") or checked.get("review_original_text")
                    or checked.get("evidence_text"))
        keywords = candidate.get("matched_keywords") or candidate.get("keyword_hits") or []
        channels = candidate.get("retrieval_channels") or []
        cards.append(f"""
        <article class="candidate {css}" data-status="{_h(status)}" data-search="{_h(' '.join(map(str, [candidate.get('candidate_id'), candidate.get('chunk_id'), heading, anchor_text, checked.get('reason')])))}">
          <div class="candidate-head">
            <div><span class="rank">#{_h(rank)}</span><span class="badge {css}">{_h(status)}</span>
              <strong>{_h(candidate.get('candidate_id'))}</strong></div>
            <button class="toggle" type="button">收起</button>
          </div>
          <div class="meta-grid">
            <div><b>Chunk</b><span>{_h(candidate.get('chunk_id'))} · {_h(candidate.get('chunk_type'))}</span></div>
            <div><b>PDF页码</b><span>{_h(_pages(checked, candidate)) or '无'}</span></div>
            <div><b>章节定位</b><span>{_h(' / '.join(map(str, heading))) or '无'}</span></div>
            <div><b>召回分数</b><span>RRF {_h(candidate.get('rrf_score'))} · BM25 {_h(candidate.get('bm25_score'))}</span></div>
          </div>
          <div class="candidate-body">
            <section><h3>召回片段原文</h3>{_text(anchor_text)}</section>
            <section><h3>审查结果</h3>
              <div class="result-line"><span class="badge {css}">{_h(status)}</span>
              原始判断：{_h(checked.get('raw_result') or raw.get('审查结果'))} · 校验状态：{_h(checked.get('validation_status'))}</div>
              <h4>判断原因</h4>{_text(checked.get('reason') or raw.get('判断理由'))}
            </section>
            <section><h3>违规／复核证据原文</h3>{_text(evidence, '无；该候选未判为违规或人工复核')}</section>
            <details><summary>查看送审完整上下文</summary>{_text(reviewed_text)}</details>
            <details><summary>查看召回与来源信息</summary>
              <dl>
                <dt>命中关键词</dt><dd>{_h(keywords) or '无'}</dd>
                <dt>命中子规则ID</dt><dd>{_h(candidate.get('matched_rule_ids')) or '无'}</dd>
                <dt>命中边界ID</dt><dd>{_h(candidate.get('matched_boundary_ids')) or '无'}</dd>
                <dt>召回通道</dt><dd>{_h(channels) or '无'}</dd>
                <dt>Source IDs</dt><dd>{_h(candidate.get('source_block_ids')) or '无'}</dd>
                <dt>证据页码</dt><dd>{_h(checked.get('evidence_page_numbers')) or '无'}</dd>
                <dt>校验错误</dt><dd>{_h(checked.get('validation_errors')) or '无'}</dd>
              </dl>
            </details>
          </div>
        </article>""")

    document_result = final.get("审查结果") or summary.get("document_result") or "UNKNOWN"
    client_section = _client_result_section(
        client_content,
        str(document_result),
        client_response.get("usage") or {},
    )
    filename = summary.get("filename") or document_root.name
    output = output or document_root / "final_review_report.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    page = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_h(filename)} - 召回与审查合并报告</title>
<style>
:root{{--bg:#f3f5f8;--card:#fff;--ink:#172033;--muted:#637083;--line:#dce2ea;--blue:#2463eb;--pass:#17834b;--fail:#c83232;--review:#b46a00}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.65 "Microsoft YaHei",system-ui,sans-serif}}
.wrap{{max-width:1280px;margin:auto;padding:26px}} header,.candidate{{background:var(--card);border:1px solid var(--line);border-radius:12px;box-shadow:0 2px 8px #18243a0a}}
header{{padding:24px;margin-bottom:18px}} h1{{font-size:24px;margin:0 0 8px}} h2{{font-size:16px;margin:0 0 12px}} h3{{font-size:15px;margin:18px 0 7px;color:#263a5e}} h4{{margin:14px 0 4px}}
.client-result-panel{{background:var(--card);border:1px solid var(--line);border-radius:12px;box-shadow:0 2px 8px #18243a0a;padding:20px;margin-bottom:18px}} .section-title{{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}} .section-title h2{{font-size:20px;margin:0}} .section-title p{{color:var(--muted);margin:4px 0 0}} .usage{{white-space:nowrap;color:var(--muted);background:#eef2f8;padding:6px 10px;border-radius:8px}}
.client-item{{border:1px solid var(--line);border-left:5px solid #8b97a8;border-radius:10px;margin-top:16px;overflow:hidden}} .client-item.pass{{border-left-color:var(--pass)}} .client-item.fail{{border-left-color:var(--fail)}} .client-item.review{{border-left-color:var(--review)}} .client-body{{padding:0 16px 18px}} .terms{{margin:0;padding:10px 12px 10px 32px;background:#f8fafc;border:1px solid #e4e9f0;border-radius:8px}} .client-evidence h4{{color:#425777}} .empty{{padding:12px;color:var(--muted);background:#f8fafc;border-radius:8px}}
.summary{{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}} .stat{{padding:8px 14px;border-radius:9px;background:#eef2f8}} .stat b{{font-size:18px;margin-right:5px}}
.toolbar{{position:sticky;top:0;z-index:5;background:#f3f5f8ed;backdrop-filter:blur(6px);padding:10px 0;display:flex;gap:8px;flex-wrap:wrap}}
input{{flex:1;min-width:260px;padding:10px 12px;border:1px solid var(--line);border-radius:8px}} button{{border:1px solid var(--line);background:white;border-radius:7px;padding:7px 10px;cursor:pointer}} button.active{{background:var(--blue);color:white;border-color:var(--blue)}}
.candidate{{margin:12px 0;border-left:5px solid #8b97a8;overflow:hidden}} .candidate.pass{{border-left-color:var(--pass)}} .candidate.fail{{border-left-color:var(--fail)}} .candidate.review{{border-left-color:var(--review)}}
.candidate-head{{display:flex;justify-content:space-between;align-items:center;padding:14px 16px;border-bottom:1px solid var(--line);gap:12px}} .rank{{font-size:18px;font-weight:700;margin-right:9px}}
.badge{{display:inline-block;color:white;border-radius:999px;padding:2px 9px;margin-right:8px;font-weight:700}} .badge.pass{{background:var(--pass)}} .badge.fail{{background:var(--fail)}} .badge.review{{background:var(--review)}} .badge.unknown{{background:#6c7685}}
.meta-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px 20px;padding:12px 16px;background:#fafbfc}} .meta-grid div{{display:flex;gap:8px}} .meta-grid b{{white-space:nowrap;color:var(--muted)}}
.candidate-body{{padding:0 16px 18px}} .prose{{white-space:pre-wrap;background:#f8fafc;border:1px solid #e4e9f0;border-radius:8px;padding:12px;max-height:520px;overflow:auto}} .result-line{{margin:6px 0}}
details{{margin-top:12px;border:1px solid var(--line);border-radius:8px;padding:9px 11px}} summary{{cursor:pointer;font-weight:700}} dl{{display:grid;grid-template-columns:120px 1fr;gap:5px 12px}} dt{{font-weight:700;color:var(--muted)}} dd{{margin:0;overflow-wrap:anywhere}}
.rule{{white-space:pre-wrap;max-height:320px;overflow:auto;background:#f8fafc;padding:12px;border-radius:8px;border:1px solid var(--line)}} .hidden{{display:none!important}} footer{{color:var(--muted);padding:20px 4px}}
@media(max-width:760px){{.wrap{{padding:12px}}.meta-grid{{grid-template-columns:1fr}}}}
@media print{{.toolbar,.toggle{{display:none}}.candidate{{break-inside:avoid;box-shadow:none}}.prose{{max-height:none}}}}
</style></head><body><div class="wrap">
<header><h1>召回片段与审查结果合并报告</h1>
  <div><b>文档：</b>{_h(filename)}　<b>Document ID：</b>{_h(document_root.name)}　<b>审查项：</b>{_h(candidates[0].get('rule_name') if candidates else '')}</div>
  <div class="summary"><span class="stat"><b class="{_status_class(str(document_result))}">{_h(document_result)}</b>文档结果</span>
    <span class="stat"><b>{len(rows)}</b>召回/审查数</span><span class="stat"><b>{counts.get('PASS',0)}</b>PASS</span>
    <span class="stat"><b>{counts.get('FAIL',0)}</b>FAIL</span><span class="stat"><b>{counts.get('REVIEW',0)}</b>REVIEW</span></div>
  <details><summary>查看本次审查规则完整内容</summary><div class="rule">{_h(rule_context)}</div></details>
</header>
{client_section}
<div class="toolbar"><input id="search" placeholder="搜索片段、章节、Chunk ID或判断原因">
  <button class="filter active" data-filter="ALL">全部 {len(rows)}</button><button class="filter" data-filter="PASS">PASS {counts.get('PASS',0)}</button>
  <button class="filter" data-filter="FAIL">FAIL {counts.get('FAIL',0)}</button><button class="filter" data-filter="REVIEW">REVIEW {counts.get('REVIEW',0)}</button></div>
<main>{''.join(cards)}</main>
<footer>报告由本地流水线根据 client_content_result.json、client_api_response.json、candidate_top50.jsonl、predictions.jsonl 和 validated_predictions.jsonl 自动合并生成。缺失验证结果：{_h(missing) or '无'}。</footer>
</div><script>
const cards=[...document.querySelectorAll('.candidate')],search=document.querySelector('#search');let filter='ALL';
function apply(){{const q=search.value.trim().toLowerCase();cards.forEach(c=>c.classList.toggle('hidden',!((filter==='ALL'||c.dataset.status===filter)&&(!q||c.dataset.search.toLowerCase().includes(q)))));}}
search.addEventListener('input',apply);document.querySelectorAll('.filter').forEach(b=>b.onclick=()=>{{document.querySelectorAll('.filter').forEach(x=>x.classList.remove('active'));b.classList.add('active');filter=b.dataset.filter;apply();}});
document.querySelectorAll('.toggle').forEach(b=>b.onclick=()=>{{const body=b.closest('.candidate').querySelector('.candidate-body');body.classList.toggle('hidden');b.textContent=body.classList.contains('hidden')?'展开':'收起';}});
</script></body></html>"""
    output.write_text(page, encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Build combined retrieval and Judge HTML report")
    parser.add_argument("--document-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = build_combined_report(args.document_root.resolve(), args.output.resolve() if args.output else None)
    print(result)


if __name__ == "__main__":
    main()
