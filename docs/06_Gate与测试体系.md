# 招标文件智能审查系统

> 文档状态：【当前实现】；工程名：`tender_review_system`；基线日期：2026-09-14。除明确标注【历史探索】【曾用方案】【规划中】【未来工作】外，本文描述当前真实代码与产物。路径采用逻辑根，不绑定开发机器。

逻辑根约定：`<PARSER_ROOT>` 为解析组件，`<CHUNK_ROOT>` 为切分组件，`<RETRIEVAL_ROOT>` 为召回与证据后处理组件，`<JUDGE_ROOT>` 为冻结审查运行组件，`<PIPELINE_ROOT>` 为本地编排组件，`<OUTPUT_ROOT>` 为运行产物根，`<RULE_STORE>` 为规则库，`<FROZEN_PROGRAM>` 为冻结提示程序。

## 1. Gate总表

| Stage | Gate | 目的 | Hard Failure | Warning | 报告 |
|---|---|---|---|---|---|
| Parser | Source/GoodMD Generic | 原文闭合与安全回退 | missing、duplicate、unknown、covered leak、GoodMD失败 | heading风险 | Parser_Generic_Gate.json |
| GoodMD | Render Validation | render/source map一致 | coverage不足、map无效 | 结构保守 | PF2_GoodMD_Validation.json |
| Chunk | Chunk Generic | Chunk可召回可追溯 | 空集、缺页码/source、coverage不足 | MERGEABLE_SHORT | Chunk_Generic_Gate.json |
| Chunk Regression | C1.1 Final | 固定样本闭合 | 历史基线不符 | 非内容空表 | C1_1_Final_Gate_Report.json |
| Retrieval | Retrieval Generic | Top-K身份与来源 | 重复、未知、TOC、页码缺失、输入数不符 | 短候选 | Retrieval_Generic_Gate.json |
| Judge Input | Frozen Runner Check | 输入字段与rule一致 | ID重复、rule context不一致、token budget不足 | 无页码由下游补 | run_manifest.json |
| Judge Technical | Judge Generic | 技术失败显式 | 缺prediction、技术失败变PASS | technical→REVIEW | Judge_Generic_Gate.json |
| Evidence | Grounding | FAIL证据可定位 | validated FAIL未ground | ungrounded→REVIEW | R2_Evidence_Grounding_Report.json |
| Document | Aggregation | 优先级和去重 | 非法result、dedup失败 | REVIEW | R2_Final_Gate_Report.json |
| Pipeline | StageResult | Fail Fast/Resume | 任一Hard Gate FAIL | SKIPPED表示resume | pipeline_manifest.json |
| Branding/Sanitization | Documentation Audit | 交付清洁 | 禁词、绝对路径、密钥 | 缺证据 | Documentation_Consistency_Report.json |

## 2. Generic与Regression

Generic Runtime Gate面向任意PDF，只判断正计数、闭合、唯一性、来源和安全回退。DOC011 Regression Gate固定172物理页、169 active页、1946 source IDs、273 Chunk、230 TEXT、43 TABLE、Top50。page29/74/127/142均只属于DOC011案例，不得作为通用文件成功条件。

## 3. 测试体系

| 分组 | 当前/阶段数 | 说明 |
|---|---:|---|
| Parser当前代码 | 181 collected | 含A1、PF-1、PF-2、PF-2.1、PF-2.2、三类VLM实验与泛化测试 |
| PF-2阶段报告 | 116 PASS | 当时冻结范围，历史数字 |
| Chunk当前目录 | 57 collected | 其中含13项召回相关测试 |
| C1.1阶段报告 | 44 PASS | 19项C1+25项C1.1；未执行retrieval |
| Retrieval/R2当前目录 | 20 collected | 8项R1契约+12项R2证据 |
| R1阶段报告 | 18 PASS | 当时10项既有+8项契约 |
| R2阶段报告 | 30 PASS | 10项冻结Judge+20项R1/R2 |
| Pipeline当前代码 | 22 PASS | 历史集成报告记录21，新增合并HTML测试后为22 |

测试重点覆盖：Block ID完整性、covered排除、VLM fallback、page29枚举、page142 scope、GoodMD文本不重写、Heading hard boundary、span表格、空表登记、provenance、TOC排除、RRF、anchor/context、未ground FAIL转REVIEW、聚合优先级、Resume指纹、批处理失败隔离和密钥脱敏。

## 4. 质量语义

Gate PASS表示数据契约满足，不表示所有业务判断正确。Parser覆盖100%不表示结构完美；Retrieval Top50闭合不表示召回率100%；Judge grounded不表示法律判断正确。业务准确率必须通过人工Gold独立评估。
