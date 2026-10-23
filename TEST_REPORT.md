# 离线测试报告

整理日期：2026-10-23

## 已通过

| 模块 | 结果 |
|---|---:|
| `01_pdf_docling/docling_postprocess/tests` | 154 passed |
| `03_md_chunk/tests` + `04_retrieval_judge/tests` | 74 passed, 3 skipped |
| `05_pipeline_local/tests` | 145 passed, 1 dependency deprecation warning |
| 合计 | **373 passed, 3 skipped, 0 active-chain failed** |

3项跳过是可选DOC0181历史产物回归；仓库有意不包含真实招标文档运行产物。若需执行，可设置：

- `TENDER_REVIEW_DOC0181_RETRIEVAL_ROOT`
- `TENDER_REVIEW_DOC0181_CHUNKS`

## 未纳入通过数

`02_stage1_stage2`是早期双源解析实验代码，不在当前正式编排链路中。其测试保留旧导入布局，并需要额外的`lxml`等环境依赖；本次未将其结果计入可运行主链路验证，也没有为了上传仓库而重写该历史模块。

## 安全边界

以上测试没有调用模型API、没有访问外部网络、没有重新运行PDF解析，也没有使用真实API Key。
