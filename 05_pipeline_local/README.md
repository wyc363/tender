# Local End-to-End Tender Review Pipeline

该目录把现有冻结 Parser、Chunk、Retrieval 和 Judge 封装为顺序流水线。当前支持单 PDF、单规则 `RP_REGION_INDUSTRY_AWARD`。业务源码不依赖开发机盘符；Windows 和 Linux 的差异通过 `config/pipeline.local.yaml`、`.env` 和 Python 运行时路径处理。

## 首次配置

复制 `.env.example` 为 `.env`，填写 Parser/Judge 的 endpoint 和 API Key。不要提交 `.env`。如果 Docling 使用独立 Python 环境，在 `.env` 中设置 `TENDER_REVIEW_DOCLING_PYTHON`；其余 Stage 留空时沿用启动 Pipeline 的 Python。

配置优先级为：当前进程环境变量 > 对应 `.env` 文件 > YAML 默认值。迁移机器通常只需调整以下变量：

- `TENDER_REVIEW_PROJECT_ROOT`：交付包根目录；
- `TENDER_REVIEW_OUTPUT_ROOT`：所有文档输出根目录；
- `TENDER_REVIEW_*_PYTHON`：各 Stage 的 Python 可执行文件；
- `PARSER_VLM_*`、`MODEL_*`：Parser 与 Judge 模型连接及冻结运行参数。

未设置路径变量时，项目根目录按本文件上两级的 `tender_review_system` 解析。所有业务资源均使用项目根目录下的相对路径。

## Check-only（零 API）

```powershell
cd "<PROJECT_ROOT>\05_pipeline_local"

& "<JUDGE_ROOT>\.venv\Scripts\python.exe" -m review_pipeline.cli `
  --pdf "D:\path\input.pdf" `
  --document-id DOC012 `
  --rule-id RP_REGION_INDUSTRY_AWARD `
  --check-only
```

## 单文档完整运行

```powershell
& "<JUDGE_ROOT>\.venv\Scripts\python.exe" -m review_pipeline.cli `
  --pdf "D:\path\input.pdf" `
  --document-id DOC012 `
  --rule-id RP_REGION_INDUSTRY_AWARD `
  --resume
```

省略 `--document-id` 时自动使用 `DOC_<PDF SHA256前8位>`。可用 `--stop-after parser|chunk|retrieval|judge` 排错。DOC011 回归增加：

```powershell
  --regression-profile DOC011
```

每个 Stage 只有在输入 SHA256、Stage 代码指纹、配置指纹全部一致且产物存在时才恢复。上游失败会立即停止，不会生成静默 PASS。

完整运行后，文档输出根目录中的 `final_review_report.html` 会把 Top50 召回片段和逐条 Judge 结果合并展示。报告包含召回排名、片段原文、页码、章节、规则全文、模型判断、证据原文、判断原因、命中关键词和来源 ID，并支持搜索及 PASS/FAIL/REVIEW 筛选。

流水线还会自动生成两份甲方接口格式文件：

- `client_content_result.json`：`choices[0].message.content` 解码后的业务对象；
- `client_api_response.json`：包含 `id/model/usage/choices/log_file` 的完整响应，其中 `message.content` 为 JSON 字符串。

接口字段 `name/type/rule/risk_level/policy` 来自 `config/rule_catalog.compiled.json`。该目录由 `tools/compile_rule_catalog.py` 从《招标文件审查规则导出20260804.xlsx》编译生成，共包含31个审查点和138条叶子规则；运行 Pipeline 时不读取 Excel。当前流水线只查询 `RP_REGION_INDUSTRY_AWARD`，不会执行其他30项。接口 `result` 按审查点聚合，当前固定为一条，`index=12`；其 `text/page/reason/is_pass/term` 由该审查点下的候选、Judge 与 Finding 汇总得到。PASS 时最多保留 `client.pass_evidence_top_n` 条代表证据，默认3条。内部 `REVIEW` 与校验码完整保留；甲方布尔字段跟随 Gate 前模型原始 PASS/FAIL。`scope` 使用实际执行方式“切分审核”。模型原始判断为 FAIL 的 FAIL/REVIEW 结果在显式使用 `--enrich-fail` 时额外调用一次轻量模型生成 `suggestions`。其他固定字段不交给模型判断。

规则 Excel 更新后，在本目录重新编译：

```powershell
& "<JUDGE_ROOT>\.venv\Scripts\python.exe" .\tools\compile_rule_catalog.py `
  --input "<PROJECT_ROOT>\datasets\招标文件审查规则导出20260804.xlsx"
```

编译产物包括运行时 JSON、人工预览 Markdown 和校验报告。只有编译校验通过的 JSON 才供接口适配器读取。

## 仅重跑 REVIEW 候选

先检查候选，不调用 API：

```powershell
& $python -m review_pipeline.retry_review_cli `
  --document-root "D:\path\to\outputs\tender_review_system\DOC_XXXXXXXX" `
  --check-only
```

只调用 REVIEW 候选，并与其余旧预测安全合并：

```powershell
& $python -m review_pipeline.retry_review_cli `
  --document-root "D:\path\to\outputs\tender_review_system\DOC_XXXXXXXX" `
  --run `
  --enrich-fail
```

原结果自动存档到 `selective_retry_archive`。过滤输入、重跑结果和合并后的完整预测保存在
`selective_retry_work`，执行报告为 `selective_review_retry_report.json`。只有 REVIEW 候选调用
Judge；证据校验、文档聚合、接口 JSON 和 HTML 基于合并后的完整候选集在本地重建。

## Batch

复制并编辑 `config/batch_local.example.json`，然后顺序执行：

```powershell
& "<JUDGE_ROOT>\.venv\Scripts\python.exe" -m review_pipeline.batch `
  --manifest ".\config\batch_local.json" `
  --rule-id RP_REGION_INDUSTRY_AWARD `
  --resume
```

单个 PDF 失败会记录 FAILED 并继续下一项。汇总写入 `I1_L_Batch_Summary.csv/json`，空白人工评价表写入 `I1_L_Manual_Evaluation.csv`。

## 持久化 Job（阶段3）

Job 数据库默认位于 `<output_root>/.state/jobs.sqlite3`，也可以通过
`TENDER_REVIEW_JOB_DB` 指向服务器运行时目录。数据库只保存任务元数据，PDF、
Candidate、Judge输出和正式结果仍保存在文档输出目录。

提交本地 PDF 只会创建 `QUEUED` 任务，不会运行 Pipeline，也不会调用模型：

```powershell
& $python -m review_pipeline.job_cli submit `
  --pdf "D:\path\input.pdf" `
  --chat-id "可选的甲方chatId"
```

运行一个独立 Worker，处理队列中的任务：

```powershell
& $python -m review_pipeline.job_worker --once
```

去掉 `--once` 后持续轮询。Worker 调用现有 `PipelineOrchestrator`，并通过可选进度
回调更新 `INPUT_CHECK/DOCLING/PARSER/CHUNK/RETRIEVAL/JUDGE/AGGREGATION/CLIENT_OUTPUT`。
中断后由下一次 Worker 启动把 Heartbeat 超时的 `RUNNING` 任务标记为
`INTERRUPTED`，必须显式重新入队：

```powershell
& $python -m review_pipeline.job_cli requeue JOB_xxx
```

查询任务和结果：

```powershell
& $python -m review_pipeline.job_cli status JOB_xxx
& $python -m review_pipeline.job_cli events JOB_xxx
& $python -m review_pipeline.job_cli result JOB_xxx
```

只有 Pipeline 返回 `COMPLETED` 且 `review_point_result.json`、
`client_content_result.json`、`client_api_response.json` 同时存在，Job 才会进入
`COMPLETED/DONE`。输入缺失、SHA变化、聚合证据异常、Pipeline异常或正式结果缺失
都会进入 `FAILED`，不会转换为业务 PASS/FAIL。

## FastAPI HTTP 服务骨架（阶段4）

安装 HTTP 层依赖：

```powershell
& $python -m pip install -r .\requirements_integration.txt
```

启动本地 HTTP 服务：

```powershell
& $python -m review_pipeline.http.server
```

服务默认只监听 `127.0.0.1:8000`，提供以下接口：

- `GET /health`：检查配置和 SQLite Job Store，不运行 Pipeline；
- `POST /api/v1/review/jobs`：校验甲方请求并创建 `QUEUED` Job；
- `GET /api/v1/review/jobs/{job_id}`：查询 Job 状态与 Stage；
- `GET /api/v1/review/jobs/{job_id}/result`：仅在 Job 完成后读取现有
  `client_api_response.json`。

阶段5已将生产默认Resolver切换为`SafeFileUrlResolver`。合法公网HTTP/HTTPS PDF会流式下载到
`paths.staging_root`，验证PDF magic并按SHA256内容寻址落盘，再交给现有JobService创建任务。
默认拒绝私网、loopback、link-local、multicast、unspecified和reserved地址；每次重定向都会重新
校验URL和DNS结果。下载大小默认上限64 MiB，连接/读取超时、重定向次数、可信私网Host/CIDR均在
`file_url`配置节管理，可信列表默认为空。HTTP App不会启动Worker，也不会直接调用Pipeline；需要
另开终端运行阶段3的Worker：

```powershell
& $python -m review_pipeline.job_worker
```

结果接口只将现有响应顶层 `id` 映射为提交时的 `chatId`，保持
`choices[0].message.content` 为 JSON 字符串，并保持其中的审查业务字段不变。HTTP 参数位于
`config/pipeline.local.yaml` 的 `http` 节；默认不启用 CORS，也不绑定公网地址。

下载器只使用完整URL中自带的签名信息，不接收或转发任意Authorization Header、Cookie或自定义
Header。URL query不会写入日志。staging文件必须保留到Worker完成任务；当前阶段未启用自动清理。

## 配置与安全

路径和非秘密默认参数集中在 `config/pipeline.local.yaml`，更换 PDF 无需修改 Python。Parser 使用 `PARSER_VLM_*`，Judge 使用 `MODEL_*`，两套变量继续分离。`review.enabled_review_points` 当前只启用 `RP_REGION_INDUSTRY_AWARD`，`client.pass_evidence_top_n` 默认为3。配置指纹只记录脱敏后的有效模型设置，不写入 API Key；日志会对疑似 `api_key=` 内容脱敏。

Docling 环境需要 ONNX Runtime，避免 RapidOCR 在 Windows 上回退到不稳定的 Torch backend：

```powershell
& $env:TENDER_REVIEW_DOCLING_PYTHON -m pip install -r ".\requirements_docling_integration.txt"
```

