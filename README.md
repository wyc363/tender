# Tender Review System — V0 单审查点全链路

这是“RP12 V2准确率优化之前”的基线工程仓库。它保留已经跑通的单审查点全链路、持久化Job、HTTP接口与Linux部署代码，不包含后续 `v2优化/`、Full31实验和任何真实运行数据。

## 当前业务范围

- 正式启用审查点：`RP_REGION_INDUSTRY_AWARD`
- 甲方结果索引：`index=12`
- 默认召回：BM25/RRF Top50
- 默认Judge：冻结Prompt + `qwen3-8b`（模型参数可通过环境变量配置）
- 输出：Review Point聚合结果、甲方接口JSON、HTML审查展示

## 全链路

```text
PDF / file_url
→ 安全下载与Staging
→ Docling + Parser结构恢复
→ Good Markdown
→ Markdown Chunk
→ 单审查点BM25/RRF Top50
→ Candidate Judge
→ Evidence Grounding / Gate
→ Review Point Aggregation
→ Client JSON / HTTP Response
```

## 目录

| 目录 | 作用 |
|---|---|
| `01_pdf_docling` | PDF读取、页眉页脚处理、页面图片、结构恢复与Good Markdown |
| `02_stage1_stage2` | 早期双源结构解析与相关测试 |
| `03_md_chunk` | Markdown Event Parser、TEXT/TABLE Chunk及Provenance |
| `04_retrieval_judge` | 单审查点召回、Judge、Grounding与Gate |
| `05_pipeline_local` | 全链路编排、聚合、Job、Worker、FastAPI和同步接口 |
| `05_dependencies` | 分阶段依赖清单和环境变量示例 |
| `06_sample_artifacts` | 仅保留运行必需的冻结Prompt和Rule Store |
| `deploy/linux` | Linux启动脚本、systemd服务样例和部署配置 |
| `docs` | 架构、Schema、运行与故障排查文档 |

## 配置

1. 复制环境变量模板：

```powershell
Copy-Item "05_pipeline_local/.env.example" "05_pipeline_local/.env"
```

2. 在本地 `.env` 中填写Parser与Judge的Endpoint、API Key和模型名。`.env`已被Git忽略，禁止提交。
3. `05_pipeline_local/config/pipeline.local.yaml`使用相对路径及环境变量，可在Windows和Linux迁移。

## 本地检查

```powershell
$env:PYTHONPATH = "$PWD/05_pipeline_local;$PWD/01_pdf_docling;$PWD/03_md_chunk;$PWD/03_md_chunk/src;$PWD/04_retrieval_judge;$PWD/04_retrieval_judge/src"
python -m review_pipeline.cli --pdf "D:/path/to/input.pdf" --rule-id RP_REGION_INDUSTRY_AWARD --check-only
```

`--check-only`只检查路径、配置和运行条件，不调用模型。真实运行前请阅读 `05_pipeline_local/README.md`。

## HTTP接口

- `GET /health`
- `POST /api/v1/review/jobs`
- `GET /api/v1/review/jobs/{job_id}`
- `GET /api/v1/review/jobs/{job_id}/result`
- `POST /api/v1/review`（同步甲方接口）

FastAPI不自动启动Worker。API与Worker需要作为两个独立进程运行。

## 测试

测试默认使用Fake/Test Double；不要在测试环境中填写真实Key。各模块可分别执行：

```powershell
python -m pytest 01_pdf_docling/docling_postprocess/tests
python -m pytest 03_md_chunk/tests
python -m pytest 04_retrieval_judge/tests
python -m pytest 05_pipeline_local/tests
```

## 仓库边界

本仓库没有包含：

- `.env`与任何真实API Key；
- 原始PDF和甲方/专家Excel；
- 历史运行输出、SQLite数据库、raw responses和模型缓存；
- DOC011/DOC0181正文与Candidate样例；
- RP12 V2、Stage 3B～4F、方案B/C及Full31优化实验。

本仓库是可运行的V0工程基线，不代表全部31项规则已经完成准确率验证。


