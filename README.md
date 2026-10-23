# Tender Review — V0 Deployment

这是已经跑通的单审查点部署版，只保留服务器运行所需代码。仓库不包含V2优化、Full31实验、历史测试工程、原始PDF、专家Excel和运行输出。

## 当前范围

- 审查点：`RP_REGION_INDUSTRY_AWARD`
- 甲方索引：`index=12`
- 召回：BM25/RRF Top50
- Judge：冻结Prompt，默认`qwen3-8b`
- Parser VLM：默认`qwen3.7-flash`
- 对外服务：FastAPI同步接口、Job接口、独立Worker

## 实际运行链路

```text
PDF / file_url
→ SafeFileUrlResolver与Staging
→ Docling document.json
→ Parser VLM与确定性结构恢复
→ good.md + source_map.jsonl
→ Markdown Chunk + Provenance
→ BM25/RRF Top50
→ Frozen Prompt Judge
→ Evidence Grounding / Gate
→ Review Point Aggregation
→ client_api_response.json / HTTP响应
```

## 目录

| 路径 | 用途 |
|---|---|
| `01_pdf_docling` | Docling入口及正式Parser运行模块 |
| `03_md_chunk` | Markdown Chunk与来源闭合 |
| `04_retrieval_judge` | Top50召回、Judge、Evidence Grounding |
| `05_pipeline_local` | Pipeline、Job、Worker、FastAPI、结果适配 |
| `06_sample_artifacts` | 冻结Prompt与Rule Store，属于运行资产 |
| `deploy/linux` | Linux依赖、启动脚本与systemd样例 |

## 配置

```powershell
Copy-Item "05_pipeline_local/.env.example" "05_pipeline_local/.env"
```

在 `.env` 中配置：

- `PARSER_VLM_API_BASE`
- `PARSER_VLM_API_KEY`
- `PARSER_VLM_MODEL`
- `MODEL_API_BASE`
- `MODEL_API_KEY`
- `MODEL_NAME`

真实 `.env` 已被Git忽略，禁止提交。

## Windows本地检查

```powershell
$env:PYTHONPATH = "$PWD/05_pipeline_local;$PWD/01_pdf_docling;$PWD/03_md_chunk;$PWD/03_md_chunk/src;$PWD/04_retrieval_judge;$PWD/04_retrieval_judge/src"
python -m review_pipeline.cli --pdf "D:/path/to/input.pdf" --rule-id RP_REGION_INDUSTRY_AWARD --check-only
```

`--check-only`不会调用模型。正式运行时将最后的`--check-only`改为`--run --resume`。

## HTTP与Worker

在两个终端中分别启动：

```powershell
cd 05_pipeline_local
python -m review_pipeline.http.server --config config/pipeline.local.yaml
```

```powershell
cd 05_pipeline_local
python -m review_pipeline.job_worker --config config/pipeline.local.yaml
```

主要接口：

- `GET /health`
- `POST /api/v1/review`
- `POST /api/v1/review/jobs`
- `GET /api/v1/review/jobs/{job_id}`
- `GET /api/v1/review/jobs/{job_id}/result`

## Linux部署

服务器上建议显式克隆为`/opt/tender_review/tender_review_system`：

```bash
sudo mkdir -p /opt/tender_review
sudo chown -R "$USER":"$USER" /opt/tender_review
git clone https://github.com/wyc363/tender.git /opt/tender_review/tender_review_system
cd /opt/tender_review/tender_review_system
bash deploy/linux/bootstrap_dirs.sh
```

随后按`deploy/linux/README.md`创建虚拟环境、填写`.env`并安装systemd服务。API和Worker必须作为独立进程运行。

## 仓库不包含

- 真实API Key与`.env`；
- PDF、Excel、SQLite和模型raw response；
- DOC011/DOC0181历史运行产物；
- 早期`02_stage1_stage2`实验；
- 单元测试、阶段报告和V2准确率优化代码；
- Full31多审查点实验。

该仓库是当前单审查点部署基线，不代表31个审查点均已完成业务准确率验收。
