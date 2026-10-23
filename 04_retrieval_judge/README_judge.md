# 招标文件合规审查 DSPy V0.1

本目录使用独立 Python 3.12 虚拟环境运行零样本 DSPy Baseline。

## 数据角色

- `../outputs/v01/data/normalized_rule.json`：模型规则输入。
- `../outputs/v01/data/model_inputs.jsonl`：40 条可用模型输入；实际 payload 仅包含 `rule_context` 与 `document_text`。
- `../outputs/v01/data/gold_dataset.jsonl`：模型推理完成后才读取的人工 Gold，不得进入模型 payload。
- `../outputs/v01/data/ambiguous_gold.jsonl`：只供人工复核，不进入自动指标。

## 环境检查（不调用 API）

```powershell
.\.venv\Scripts\python.exe scripts\v01\run_baseline.py --check-only
.\.venv\Scripts\python.exe -m pytest -q
```

## API 配置

复制 `.env.example` 为 `.env`，本地填写 `MODEL_API_BASE`、`MODEL_API_KEY`、`MODEL_NAME`。`.env` 已加入 `.gitignore`。

`--check-only` 不会调用模型；真正调用必须显式使用 `--run`。运行器已经实现重试、实时落盘和断点续跑。

每次真实运行会创建独立目录 `../outputs/v01/runs/<run_id>/`，其中保存该次运行自己的 `predictions.jsonl`、`run.log`、`run_manifest.json` 和 `baseline_prompt.md`。数据文件保存在 `data/`，检查报告保存在 `audits/`。

模型调用必须显式使用 `--run`。首次只运行一条：

```powershell
.\.venv\Scripts\python.exe scripts\v01\run_baseline.py --run --limit 1
```

确认输出正常后运行三条，或断点续跑：

```powershell
.\.venv\Scripts\python.exe scripts\v01\run_baseline.py --run --limit 3 --resume
```

路径默认按项目所在位置自动推导；通常不需要把绝对路径写入 `.env`。如移动源文件或输出目录，可通过 `RULES_FILE`、`REVIEWS_FILE`、`OUTPUT_DIR` 覆盖，或使用 `--rules`、`--reviews`、`--output` 命令行参数。

## 使用冻结 Prompt JSON 直接审查召回候选

运行器只读取冻结 JSON 中的 `decide.signature.instructions`，直接调用已配置的大模型 API，不运行 DSPy Module 或优化器。模型输入为 `review_item`、`rule_context`、`document_text`。

冻结 C 组正式实验使用 `MODEL_MAX_TOKENS=1024`。候选审查运行器拒绝低于1024的输出上限，避免较长理由或多段违规原文在512 token处被截断。

先做离线检查，不调用 API：

```powershell
.\.venv\Scripts\python.exe -m src.v03_pilot5.run_frozen_prompt_judge `
  --input "<OUTPUT_ROOT>\retrieval_rule_store_v2\RP_REGION_INDUSTRY_AWARD\DOC0181\judge_inputs.jsonl" `
  --program "<OUTPUT_ROOT>\v03_pilot5\prompts\C_dspy_optimized\C_compiled_program.json" `
  --output "<OUTPUT_ROOT>\judge_dspy_optimized\RP_REGION_INDUSTRY_AWARD\DOC0181" `
  --check-only
```

首次真实调用一条：

```powershell
.\.venv\Scripts\python.exe -m src.v03_pilot5.run_frozen_prompt_judge `
  --input "<OUTPUT_ROOT>\retrieval_rule_store_v2\RP_REGION_INDUSTRY_AWARD\DOC0181\judge_inputs.jsonl" `
  --program "<OUTPUT_ROOT>\v03_pilot5\prompts\C_dspy_optimized\C_compiled_program.json" `
  --output "<OUTPUT_ROOT>\judge_dspy_optimized\RP_REGION_INDUSTRY_AWARD\DOC0181" `
  --run --limit 1
```

确认第一条输出正常后去掉 `--limit 1`。运行器逐条追加 `predictions.jsonl`，默认按 `candidate_id` 断点续跑。最终主要业务字段为 `片段原文`、`审查结果`、`违规原文`、`判断理由`。通过和不通过都必须给出判断理由；违规原文必须能逐字定位到片段原文，否则该条标记为 `EVIDENCE_NOT_GROUNDED`，不会把模型改写内容当成原文输出。页码待上游提供 PDF/Docling 页码元数据后再接入。
