# 招标文件智能审查系统

> 文档状态：【当前实现】；工程名：`tender_review_system`；基线日期：2026-09-14。除明确标注【历史探索】【曾用方案】【规划中】【未来工作】外，本文描述当前真实代码与产物。路径采用逻辑根，不绑定开发机器。

逻辑根约定：`<PARSER_ROOT>` 为解析组件，`<CHUNK_ROOT>` 为切分组件，`<RETRIEVAL_ROOT>` 为召回与证据后处理组件，`<JUDGE_ROOT>` 为冻结审查运行组件，`<PIPELINE_ROOT>` 为本地编排组件，`<OUTPUT_ROOT>` 为运行产物根，`<RULE_STORE>` 为规则库，`<FROZEN_PROGRAM>` 为冻结提示程序。

## Stage产物索引

| Stage | 文件 | 生成者 | 消费者 | 正式业务输出 | Debug | 可删除 | Resume需要 |
|---|---|---|---|---|---|---|---|
| Input | 00_input/source.pdf | input stage | Docling | 否 | 否 | 完成后可重建 | 是 |
| Docling | document.json | docling_adapter | page_blocks | 事实层 | 否 | 可重建但昂贵 | 是 |
| Docling | document.md | docling_adapter | 人工比较 | 否 | 是 | 是 | 否 |
| Parser | page_blocks.jsonl | build_page_records | PF-2.2 | 中间契约 | 否 | 可重建 | 是 |
| Parser | page_images/ | page_image | VLM/人审 | 中间契约 | 是 | 可重建且昂贵 | 是 |
| Parser | furniture_decisions.json | furniture repair | audit | 否 | 是 | 是 | 否 |
| Parser | final_page_structures.jsonl | PF-2.2 | renderer | 中间契约 | 否 | 可重建 | 是 |
| Parser | good.md | render_goodmd | Chunk/人审 | 是 | 否 | 否 | 是 |
| Parser | good_debug.md | renderer | 人审 | 否 | 是 | 是 | 否 |
| Parser | source_map.jsonl | renderer | provenance | 审计 | 否 | 否 | 是 |
| Parser | raw_responses/ | VLM client | audit | 否 | 是 | 谨慎 | Resume可能使用 |
| Chunk | parse_events.jsonl | Event Parser | builder/audit | 否 | 是 | 是 | 否 |
| Chunk | chunks_raw.jsonl | builder | provenance | 中间 | 否 | 可重建 | 是 |
| Chunk | chunks.jsonl | enrich_chunks | Retrieval | 是 | 否 | 否 | 是 |
| Retrieval | candidate_pool.jsonl | fusion | Top-K | 否 | 是 | 是 | 否 |
| Retrieval | retrieval_candidates.jsonl | run_retrieval | context | 中间正式 | 否 | 可重建 | 是 |
| Retrieval | context_enriched_candidates.jsonl | context | input builder | 中间正式 | 否 | 可重建 | 是 |
| Retrieval | judge_inputs.jsonl | input builder | Judge | 是 | 否 | 否 | 是 |
| Judge | predictions.jsonl | frozen runner | R2 | 原始审计 | 否 | 不建议 | 是 |
| Judge | raw_responses/ | frozen runner | audit | 原始审计 | 是 | 不建议 | 部分恢复 |
| Judge | validated_predictions.jsonl | R2 | dedup | 是 | 否 | 否 | 是 |
| Judge | findings.jsonl | dedup | aggregation | 是 | 否 | 否 | 是 |
| Judge | document_review_result.json | aggregation | final projection | 是 | 否 | 否 | 是 |
| Final | final_review_result.json | finalize | 业务用户 | 是 | 否 | 否 | 是 |
| Final | final_review_report.html | report builder | 业务用户 | 是 | 否 | 可重建 | 是 |
| Pipeline | pipeline_manifest.json | orchestrator | Resume | 是 | 否 | 否 | 是 |
| Pipeline | pipeline_summary.json | orchestrator | 运营/人审 | 是 | 否 | 可重建有限 | 否 |
| Pipeline | pipeline.log | orchestrator | 排障 | 否 | 是 | 归档后可删 | 否 |
