# 通用 Docling JSON → Markdown（Stage 1–2）

输入任意支持本结构的 Docling `document.json`，输出清理 Markdown、调试 Markdown、删除决策、来源映射与校验报告。原 JSON 保持只读。仅使用 Python 标准库。

## 页眉页脚如何判断

不按固定字符串或固定像素坐标删除，也不直接裁掉页面顶部。每份文档独立建立模式，不沿用上一个文件的页眉文字。

1. **原生标签**：Docling 已识别的 furniture/page_header/page_footer 直接排除。
2. **邻接碎片**：同一页的已知页眉页脚，加上同行、水平邻接、跨页重复。支持多个碎片连锁补齐。
3. **跨页已知模板**：相同文本模式在至少两页已经标成页眉页脚，可修复其他页面同位置的漏识别，包括错误标成标题的情况。
   若某页拆分方式不同，候选与同页邻接锚点组合后，与至少两页原生页眉完整模板匹配，也可恢复仅单独出现一次的碎片。此规则不依赖具体年份或字符串。
4. **没有原生锚点**：仅对普通 text，要求位于边缘、跨页文本模式及垂直位置稳定、出现频率足够、处于最外围且与正文有间距，才推断为页眉页脚。没有足够证据的候选保留。

坐标先统一为 TOPLEFT，再除以每页宽高，适配页面尺寸变化。页眉左右位置及文字长度不要求一致。奇偶页分别计算重复覆盖率，支持镜像页眉。只归一化空白和明确页码（如“第 3 页”“Page 3 of 10”），不删除所有数字。纯数字页脚还要求数字随实际页序递增；重复的金额或常量不会仅因是数字而被排除。

正文标题、列表、图片说明没有已知模板证据时不走无锚点删除路径。与表格重叠或位于表格下的文本受保护。跨多页 provenance 的文本不自动整块删除。confidence 为规则证据等级，不是经过校准的概率。

## 单文件

在 `D:/pdf_parser_local_benchmark` 执行（将示例路径替换为实际文件）：

```powershell
python -m docling_postprocess.cli --input "D:/data/某份文档/document.json" --output-dir "D:/data/某份文档/cleaned_output"
```

任意文件名的单个 JSON 都可作为输入，不要求必须名为 document.json。

## 批量文件

```powershell
python -m docling_postprocess.cli --input "D:/pdf_parser_local_benchmark/output/docling" --output-dir "D:/pdf_parser_local_benchmark/output/docling_batch_cleaned"
```

递归查找目录下的 `document.json`，按原目录层级隔离各份输出，并生成 `batch_report.json`。一个文件失败会记录错误，继续处理其他文件；存在失败时命令返回非零状态。

## 参数与保守模式

```powershell
python -m docling_postprocess.cli --input "D:/data/document.json" --output-dir "D:/data/out" --config "D:/pdf_parser_local_benchmark/docling_postprocess/config.example.json"
```

配置可只填写需要覆盖的字段。关键默认值：

| 参数 | 默认值 | 用途 |
|---|---:|---|
| edge_band | 0.15 | 无锚点时的页面边缘候选区；只是候选范围，不是裁剪区域 |
| max_anchor_band | 0.25 | 根据本页锚点或其他页相同模板可扩展到的最大边缘范围 |
| position_tolerance | 0.018 | 同一文本模式允许的归一化垂直位置偏差 |
| horizontal_gap_ratio | 0.10 | 同行碎片水平间距相对页宽阈值，另设 24pt 下限 |
| min_repeat_pages | 3 | 推断需要的最少不同页面数 |
| anchored_repeat_fraction | 0.10 | 有邻接锚点时的文档重复阈值 |
| unanchored_repeat_fraction | 0.65 | 无锚点时的重复覆盖率（全页或所属奇偶页） |
| isolation_gap_ratio | 0.012 | 无锚点候选与正文所需间距 |
| max_block_height_ratio | 0.06 | 自动推断文本块的最大高度 |
| max_text_chars | 400 | 自动推断文本块的最大字符数 |

若文件页眉偏低，可以调整 edge_band；如果位置抖动更大，可调整 position_tolerance。放宽阈值会增加误删可能，应该查看 decisions 和 debug 输出。保守模式 `--conservative` 禁用无锚点推断，只依据原生 furniture 证据修复。

## 输出与渲染

- `cleaned.md`：正文 Markdown；列表只加无序项目符号，明确 marker 仅补一次。
- `cleaned_debug.md`：同样的正文规则，附页码和 source_ref。
- `furniture_decisions.json`：排除项与保留候选、证据、坐标、重复数。
- `render_map.json`：输出片段对应源 ref/page/bbox，字符偏移使用 Unicode 字符、左闭右开。
- `stage12_report.json`：实际配置、统计和通用来源一致性校验；不包含某份文件独有的断言。

遵循 body.children 逻辑顺序。46 个表格等数量不是代码条件；任意数量的表格均按各自结构渲染。表格采用 HTML 保留 span；源单元格坐标冲突时保留冲突文字在表后并标记布局损失。图片没有 URI 时记录警告。标题不重判，正文/表格不跨页拼接。

## 验证与适用边界

```powershell
python -m unittest docling_postprocess.test_stage12 docling_postprocess.test_generalization -v
```

测试覆盖纸张尺寸、长短页眉、不同位置、奇偶页、无原生锚点、变化页码、短文档、表头与正文保护、碎片链、批量命令和渲染一致性。

真实验证包括原 172 页文档，以及用户数据目录中另外 4 份 PDF 各第 6–9 页（共 16 页）。原文档仍排除 368 个原生 furniture 和 171 个页眉片段。16 页抽样共排除 36 个原生 furniture 和 5 个推断片段；无页眉的文件未删除内容。发现并修复了拆分粒度不一致导致的单个年份片段漏删。测试记录见 `output/generalization_pdf_samples/测试报告.md`。其余版式验证来自合成测试；尚未覆盖扫描件或这 4 份文件的全部页面。

单/双页文件、每页完全不同的页眉、复杂多行混排、严重 OCR 错误、嵌入表格结构的页眉、与正文无法从几何和重复性区分的文本，仍可能无法自动识别。算法选择保留不确定项，不能保证任何文件都零漏删、零误删。输入需包含 Docling 的文本、逻辑树及页面/provenance 信息；不支持任意格式 JSON 或从此命令直接解析 PDF。

## 代码

loader.py：只读加载与索引。furniture.py：可配置文档内推断。renderer.py：自定义 Markdown 与来源映射。cli.py：单文件/批量入口及通用报告。test_stage12.py、test_generalization.py：回归测试。

## VLM POC Phase A/A1（离线）

本阶段只准备逐页结构和页面图片，不包含任何模型客户端，也不会调用 API：

```powershell
python -m docling_postprocess.vlm_cli `
  --pdf "D:\data\input.pdf" `
  --json "D:\data\document.json" `
  --output-dir "D:\data\a1_output" `
  --vlm-mode all `
  --check-only
```

输出：

- `page_blocks.jsonl`：每个 PDF 物理页一条记录。原始 text、table、页码、归一化 bbox 和 source_ref 均保留；已由 table 表达的重复 text 标记为 `COVERED_BY_TABLE`。
- `page_images/page_0001.jpg ...`：宽 1400 px、quality 85 的页面图，重复执行时按 PDF SHA256 和渲染参数复用缓存。
- `page_images/manifest.json`：每张图的尺寸、大小和缓存状态。
- `furniture_decisions.json`：本次现场生成的页眉页脚排除证据和待复核候选。
- `a1_check_report.json`：PDF/JSON 页数与页面尺寸对应、block 覆盖、唯一 ID、表格 HTML、图片数、输出目录和零 API 调用检查。

`--vlm-mode` 在本阶段只计算未来调用页数。`--check-only` 是必填开关；当前入口没有 `--run`，也没有导入 LiteLLM/OpenAI 客户端。改变 PDF、图片宽度或 JPEG quality 会令图片缓存失效。

验证：

```powershell
python -m unittest `
  docling_postprocess.test_stage12 `
  docling_postprocess.test_generalization `
  docling_postprocess.tests.test_page_blocks `
  docling_postprocess.tests.test_page_image -v
```

## VLM POC Phase A2（固定页结构裁决）

将本目录的 `.env.example` 复制为 `.env`，只填写 `PARSER_VLM_*`。Parser 配置与 Judge 的 `MODEL_*` 完全隔离。A2 直接读取已经验收的 A1 blocks 和 JPEG，`--run` 强制要求显式页面且单次最多 20 页：

```powershell
python -m docling_postprocess.vlm_cli `
  --pdf "D:\data\input.pdf" `
  --json "D:\data\document.json" `
  --a1-dir "D:\data\a1_output" `
  --output-dir "D:\data\a2_output" `
  --vlm-mode all `
  --pages "5,12,17,38,167" `
  --run
```

请求只包含页面 JPEG 和 active blocks。`COVERED_BY_TABLE` 不发送；`furniture_candidate` 不发送；表格只发送最多 1500 字符的 preview，`table_html` 留在本地。优先请求严格 JSON Schema；仅当服务明确报告兼容问题时退化为 `json_object + 本地严格校验`，退化路径写入逐页结果和 usage 报告。API、JSON 或语义校验失败会按页记录并继续。

A2 输出为 `page_structures.jsonl`、`raw_responses/`、`request_audit/`、`vlm_usage_report.json`、`a2_check_report.json` 和 `A2验收说明.md`。本阶段不生成 Markdown、source map、chunks 或审查结果。
