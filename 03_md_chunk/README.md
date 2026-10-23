# Markdown-Preserving Chunker

独立从已有 Markdown 生成 `chunks.jsonl`，不依赖旧文档树或 PDF 流程。Heading 与 HTML Table 是硬边界；正文按段落和确定性长度规则切分；表格展开 rowspan/colspan 后按业务行切分。仅依赖 Python 标准库。

## 运行

```powershell
python scripts/run_md_chunking.py --input "D:/path/document.md" --document-id DOC0181 --output "D:/path/output" --strict
```

默认长度参数：target 1000、soft max 1400、hard max 1800、min 300。可用 `--target-chars`、`--soft-max-chars`、`--hard-max-chars`、`--min-chars` 覆盖。

## 输出

- `chunks.jsonl`：每行一个可直接用于 BM25 的 Chunk。
- `chunks_preview.md`：人工审阅版。
- `parse_events.jsonl`：HEADING/TEXT/TABLE 审计事件。
- `chunk_quality_report.json`：统计及 G1-G6 门禁。

本阶段不处理 PDF 页码/bbox、引用解析、补正覆盖、Embedding、Reranker 或违规判断。

## Keyword + BM25 候选召回

需要 `jieba==0.42.1` 和 `rank-bm25==0.2.2`。审查点的关键词、查询模板及 Smoke Check 均位于配置文件，不写入通用检索模块。

```powershell
python scripts/run_candidate_retrieval.py --chunks "D:/path/to/outputs/DOCXXXX/chunks.jsonl" --config "configs/retrieval/RP_REGION_INDUSTRY_AWARD.json" --output "D:/path/to/outputs/retrieval/RP_REGION_INDUSTRY_AWARD/DOCXXXX" --strict
```

输出包括各查询 BM25 排名、Keyword 排名、融合候选池、Top50 JSONL、人工预览、质量报告及人工审核 CSV。检索阶段不生成违规结论。

## 测试

```powershell
$env:PYTHONPATH="D:/path/to/tender_review_system/03_md_chunk/src"
python -m unittest discover -s tests -p "test_*.py" -v
python tests/validate_doc0181.py --output "D:/path/to/outputs/DOCXXXX"
```

## 使用单个 Rule Store 审查项增强召回

下面的命令只选择指定审查项，不会把 Rule Store 中其他审查项加入索引或查询：

```powershell
$reviewItem = "地域/行业业绩奖项及外部荣誉作为招投标加分、中标条件"

.\.venv\Scripts\python.exe scripts\run_candidate_retrieval.py `
  --chunks "<OUTPUT_ROOT>\md_chunk_poc\DOC0181\chunks.jsonl" `
  --config "configs\retrieval\RP_REGION_INDUSTRY_AWARD.json" `
  --rule-store "<OUTPUT_ROOT>\v05_boundary\rules\v2\pilot5_rule_store_v2_boundary_enhanced.json" `
  --review-item $reviewItem `
  --prompt-program "<OUTPUT_ROOT>\v03_pilot5\prompts\C_dspy_optimized\C_compiled_program.json" `
  --output "<OUTPUT_ROOT>\retrieval_rule_store_v2\RP_REGION_INDUSTRY_AWARD\DOC0181" `
  --strict
```

运行过程：

1. 精确选择一个 `review_item`，验证所有叶子和边界都属于该审查项；
2. 每条违规叶子生成一路风险 BM25 查询；
3. 每条 `boundary_guidance` 生成一路低权重边界 BM25 查询；
4. 与目标审查项的关键词通道通过加权 RRF 融合；
5. 为 Top-K 锚点加入前后各一个 Chunk，形成 `document_text`；
6. 将 `rule_context_v2`、`review_item` 和 `document_text` 写入 `judge_inputs.jsonl`。

主要输出：

- `rule_selection_manifest.json`：证明只选择了一个审查项；
- `compiled_retrieval_config.json`：从规则库编译出的实际查询；
- `bm25_query_results.jsonl`：逐查询 BM25 排名；
- `candidate_top50.jsonl`：用于审查的 Top-50；
- `candidate_top100_audit.jsonl`：漏召回审计集合；
- `judge_inputs.jsonl`：与 DSPy 签名对齐的审查输入。
