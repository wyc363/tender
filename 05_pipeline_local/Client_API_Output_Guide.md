# 甲方接口输出说明

## 输出文件

每份 PDF 完成审查后，在文档输出根目录生成：

```text
client_content_result.json
client_api_response.json
```

`client_content_result.json` 可直接阅读，结构为：

```json
{
  "result": [],
  "suggestions": "",
  "scope": "切分审核"
}
```

`client_api_response.json` 对应甲方接口完整响应。`choices[0].message.content` 是上述对象序列化得到的 JSON 字符串，调用方需要再执行一次 JSON 解析。

## 字段来源

| 输出字段 | 来源 |
| --- | --- |
| `index` | Top-K 召回片段及其 Judge 结果的顺序，从 1 开始；Top50 时固定为 1～50 |
| `name` | 审查点名称 |
| `type` | 审查类型 |
| `risk_level` | 风险等级 |
| `rule` | 审查规则说明 |
| `policy` | 政策来源 |
| `term` | 当前片段不通过时，根据该片段最强风险规则命中回填 Excel 原始违规条目；通过时为空 |
| `reason` | 当前召回片段的模型审查原因；REVIEW 保留模型原因并追加未通过的校验码 |
| `text` | 当前召回的原始 Chunk，固定为一个元素的列表 |
| `page` | 当前原始 Chunk 对应的全部 PDF 物理页码 |

运行时规则元数据使用 `config/rule_catalog.compiled.json`。该文件由 `tools/compile_rule_catalog.py` 从《招标文件审查规则导出20260804.xlsx》生成，记录源文件 SHA-256、稳定内部 ID、Excel 来源行和逐条叶子规则。程序运行时不读取 Excel。

重新编译命令：

```powershell
& $python .\tools\compile_rule_catalog.py `
  --input "D:\path\to\rules.xlsx"
```

同时生成 `config/rule_catalog.preview.md` 和 `config/rule_catalog.validation.json`。编译发生规则编号重复、固定字段冲突或必填字段缺失时失败，不覆盖有效运行逻辑。

## 结果转换

| 内部结果 | `is_pass` | 输出处理 |
| --- | --- | --- |
| PASS | `true` | `term` 为空，保留通过原因 |
| FAIL | `false` | 输出确认违规原文、页码和可定位的违规条目 |
| REVIEW | 跟随模型初始判断 | 原始 PASS 映射为 `true`，原始 FAIL 映射为 `false`；原因末尾列明未通过的校验码 |

文档级 `suggestions` 在 PASS 时为空字符串；模型已判断 FAIL 的 FAIL/REVIEW 结果由补全模型从当前审查点条目中选择 `term` 并生成修改建议。`scope` 根据当前真实执行方式固定为“切分审核”。Excel 的“扫描范围”仅作为规则适用位置提示，不直接复制到接口 `scope`。

## 当前范围

当前 Pipeline 一次只运行“地域/行业业绩奖项及外部荣誉作为招投标加分、中标条件”这一个审查点。目录查询使用稳定内部 ID `RP_REGION_INDUSTRY_AWARD`，适配器不会执行其他30项。`result` 按召回片段逐条输出；当前 Top50 检索对应50条结果，`index` 为1～50。每条的 `name/type/rule/policy/risk_level` 来自同一审查点，`text/page/reason/is_pass/term` 对应该条召回片段及 Judge 结果。

如需对已有 Pipeline 结果只执行格式转换，可在 `05_pipeline_local` 目录运行：

```powershell
& $python -m review_pipeline.client_api_cli `
  --document-root "D:\输出目录\DOC001" `
  --judge-env-file "D:\项目目录\tender_review_dspy\.env" `
  --enrich-fail
```

该命令不解析 PDF、不重新执行 Judge，只读取已有的 `04_retrieval` 和 `05_judge` 产物。传入 `--enrich-fail` 后，模型原始判断为 FAIL 的已验证 FAIL 或 REVIEW 调用一次补全模型，以选择 `term` 并生成 `suggestions`；PASS 不调用。

