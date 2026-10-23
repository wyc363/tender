# 招标文件智能审查系统

> 文档状态：【当前实现】；工程名：`tender_review_system`；基线日期：2026-09-14。除明确标注【历史探索】【曾用方案】【规划中】【未来工作】外，本文描述当前真实代码与产物。路径采用逻辑根，不绑定开发机器。

逻辑根约定：`<PARSER_ROOT>` 为解析组件，`<CHUNK_ROOT>` 为切分组件，`<RETRIEVAL_ROOT>` 为召回与证据后处理组件，`<JUDGE_ROOT>` 为冻结审查运行组件，`<PIPELINE_ROOT>` 为本地编排组件，`<OUTPUT_ROOT>` 为运行产物根，`<RULE_STORE>` 为规则库，`<FROZEN_PROGRAM>` 为冻结提示程序。

## 1. 数据链总览

| 文件 | 生成阶段 | 用途 | 主要消费者 | 正式输出 |
|---|---|---|---|---|
| document.json | Docling | 原始文档事实 | page_blocks | 中间事实 |
| page_blocks.jsonl | A1 | 逐页TEXT/TABLE facts | PF-2.2 | 中间事实 |
| good.md | Renderer | 可读稳定正文 | Event Parser、人审 | 是 |
| source_map.jsonl | Renderer | GoodMD范围到source/page | provenance | 审计sidecar |
| chunks.jsonl | C1.1 | 召回语料 | Retrieval | 中间正式接口 |
| retrieval_candidates.jsonl | R1 | Top-K anchor | Context/Judge | 中间正式接口 |
| judge_inputs.jsonl | R1 | 冻结Judge输入 | Judge | 中间正式接口 |
| predictions.jsonl | Judge | 模型原始结果 | R2 | 原始审计 |
| validated_predictions.jsonl | R2 | 保护后结果 | dedup/aggregate | 是 |
| findings.jsonl | R2 | 去重违规/复核发现 | aggregate | 是 |
| final_review_result.json | Final | 面向业务交付 | 用户/系统 | 是 |
| pipeline_manifest.json | Pipeline | Stage指纹、状态、产物 | Resume | 是 |

## 2. Schema说明

### document.json
Docling原生字典。关键集合包括texts、tables、pages和body引用；元素provenance包含page_no与bbox。示例：`{"texts":[...],"tables":[...],"pages":{...}}`。不应由下游覆盖。

### page_blocks.jsonl
每行一页。关键字段：`page_no`、`page_width/height`、`blocks`。Block关键字段：`block_id`、`kind`、`text/table_html`、`bbox`、`parser_label`、`furniture_status`、`covered_by_table`、`active`。示例：`{"page_no":17,"blocks":[{"block_id":"P0017-T001","kind":"table","active":true}]}`。

### good.md
UTF-8 Markdown。Heading用`#`层级，正文保持原文，表格保留HTML。正式版本不含调试页标签。它本身不承载全部provenance，必须与source_map配套。

### source_map.jsonl
关键字段：`render_id`、`source_line_start/end`、`page_numbers`、`source_block_ids`、`source_table_ids`、`semantic_type`、`text`。字符/行区间使用左闭右开或报告明示的行范围。

### chunks.jsonl
关键字段：`chunk_id`、`chunk_type`、`text`、`char_count`、`chapter_heading`、`section_heading`、`heading_path`、`page_numbers`、`source_block_ids`、`source_table_ids`、`heading_context_source_ids`、`document_region`。示例：`{"chunk_id":"DOC_X-C000001","chunk_type":"TEXT","page_numbers":[5],"text":"..."}`。

### retrieval_candidates.jsonl
在Chunk字段基础上增加：`candidate_id`、`rule_id`、`rank`、`retrieval_channels`、`bm25_hits`、`bm25_score/rank`、`keyword_hits/score/rank`、`rrf_score`、`matched_rule_ids`、`matched_boundary_ids`。候选text必须与冻结Chunk一致。

### judge_inputs.jsonl
模型字段：`review_item`、`rule_context`、`document_text`。审计字段：`candidate_id`、`anchor_chunk`、`context_before/after`、`anchor_chunk_id`、`context_chunk_ids`、`page_numbers`、`source_block_ids/table_ids`。模型不接收独立“页码答案”。

### predictions.jsonl
关键字段：`candidate_id`、`片段原文`、`审查结果`、`违规原文`、`判断理由`、`原文行号`、`anchor_chunk_id`、`context_chunk_ids`、`parse_status`、`raw_output`、`latency_seconds`、`usage`、`technical_error`。

### validated_predictions.jsonl
关键字段：`prediction_id`、`candidate_id`、`raw_result`、`validated_result`、`evidence_text`、`violation_original_text`、`review_original_text`、`reason`、`grounding_status`、`validation_status/errors`、`evidence_chunk_id`、`evidence_page_numbers`、`evidence_source_block_ids/table_ids`、`evidence_heading_path`、`technical_error`。

### findings.jsonl
每行一个去重发现。关键字段按当前projection包括rule、evidence、page、heading、chunk/source provenance和reason。多个candidate共享source时可合并。

### final_review_result.json
面向中文业务字段：项目编号、招标文件编号、审查点、审查结果，以及违规项或待复核项。每项含原文、PDF物理页、全部页码、章节定位、原因、Chunk ID和Source IDs。PASS时列表可为空。

### pipeline_manifest.json
顶层记录document ID、原文件名、PDF SHA、rule ID、config path和stages。每个Stage含status、started/finished、duration、artifacts、metrics、gate、error、input/stage/config fingerprint。Resume读取它，但仍核验artifact存在。

## 3. Provenance语义

content provenance证明Chunk正文来自哪些source；heading context provenance证明附加标题来自哪些source；evidence provenance证明违规原文实际落在哪个Chunk和PDF页。三者不能混用。Context页码不能覆盖anchor页码，最终证据页码使用实际匹配Chunk。
