import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = path.resolve("outputs/stage2_v2/DOC011");
const readJson = async (name) => JSON.parse(await fs.readFile(path.join(outputDir, name), "utf8"));
const readJsonl = async (name) => (await fs.readFile(path.join(outputDir, name), "utf8")).trim().split(/\r?\n/).filter(Boolean).map(JSON.parse);
const quality = await readJson("structure_quality_report.json");
const roles = await readJson("outline_roles.json");
const headings = await readJsonl("supplementary_heading_candidates.jsonl");
const tables = await readJsonl("table_groups.jsonl");

const wb = Workbook.create();
const summary = wb.worksheets.add("结构总览");
const outline = wb.worksheets.add("Outline角色");
const local = wb.worksheets.add("局部标题");
const table = wb.worksheets.add("表格组");
for (const ws of [summary, outline, local, table]) { ws.showGridLines = false; ws.getRange("A1:Z2000").format.font = { name: "Arial", size: 10, color: "#1F2937" }; }

summary.getRange("A2:H2").merge(); summary.getRange("A2").values = [["DOC011 文档结构人工审核表"]];
summary.getRange("A2:H2").format.font = { name: "Arial", size: 15, bold: true, color: "#17365D" };
summary.getRange("A3:H3").format.borders = { bottom: { style: "thin", color: "#9FBAD0" } };
summary.getRange("A5:B13").values = [
  ["指标", "结果"], ["Strict 状态", quality.status], ["PDF 页数", quality.page_count], ["Raw Outline", quality.raw_outline_count],
  ["Canonical Sections", quality.structure_counts.canonical_sections], ["Canonical Blocks", quality.structure_counts.canonical_blocks],
  ["Table Groups", quality.structure_counts.table_groups], ["Local Heading（已提升）", quality.structure_counts.local_headings_promoted],
  ["Source Atom 未归属", quality.source_atom_gate.unaccounted_count],
];
summary.getRange("D5:F9").values = [
  ["Anchor Gate", "已验证", "分母"],
  ["Structural Section", quality.anchor_gate.STRUCTURAL_SECTION.verified, quality.anchor_gate.STRUCTURAL_SECTION.denominator],
  ["Topic Section", quality.anchor_gate.TOPIC_SECTION.verified, quality.anchor_gate.TOPIC_SECTION.denominator],
  ["Attachment Section", quality.anchor_gate.ATTACHMENT_SECTION.verified, quality.anchor_gate.ATTACHMENT_SECTION.denominator],
  ["Topic + Attachment", quality.anchor_gate.TOPIC_PLUS_ATTACHMENT.verified, quality.anchor_gate.TOPIC_PLUS_ATTACHMENT.denominator],
];
summary.getRange("A15:H18").values = [
  ["审核说明", null, null, null, null, null, null, null],
  ["本表仅审核文档结构、来源追溯和范围边界，不进行违规判断。", null, null, null, null, null, null, null],
  ["优先检查：PDF48 附件三、PDF71-75 评分标准、PDF124 图片型指标表、PDF167-172 补正表。", null, null, null, null, null, null, null],
  ["人工结论请填写在各明细页的“人工复核”和“复核备注”列。", null, null, null, null, null, null, null],
];
summary.getRange("A15:H15").format = { fill: "#D9EAF7", font: { name: "Arial", bold: true, color: "#17365D" } };
for (const row of [16, 17, 18]) summary.getRange(`A${row}:H${row}`).merge();

const roleRows = [["Outline ID", "层级", "PDF页", "原标题", "归一化角色", "边界", "置信度", "人工复核", "复核备注"]];
for (const r of roles) roleRows.push([r.outline_id, r.raw_level, r.destination.page_idx + 1, r.raw_title, r.outline_role, r.boundary_strength, r.role_confidence, "待复核", ""]);
outline.getRange("A1").write(roleRows);
const headingRows = [["Local Heading ID", "PDF页", "标题", "状态", "置信度", "Native字号比", "编号信号", "人工复核", "复核备注"]];
for (const h of headings) headingRows.push([h.local_heading_id, h.page_idx + 1, h.title, h.status, h.confidence, h.evidence.native_font_ratio, h.evidence.numbering_signal, "待复核", ""]);
local.getRange("A1").write(headingRows);
const tableRows = [["Table Group ID", "起始PDF页", "结束PDF页", "Fragment数", "V2表示数", "分组状态", "图片候选", "文本预览", "人工复核", "复核备注"]];
for (const g of tables) tableRows.push([g.table_group_id, g.page_start + 1, g.page_end + 1, g.physical_fragment_source_atom_ids.length, g.logical_representation_ids.length, g.grouping_status, g.content_image_candidate, (g.text || "").slice(0, 160), "待复核", ""]);
table.getRange("A1").write(tableRows);

function styleTable(ws, cols, rows, widths) {
  const end = String.fromCharCode(64 + cols);
  ws.getRange(`A1:${end}1`).format = { fill: "#1F4E78", font: { name: "Arial", bold: true, color: "#FFFFFF" }, horizontalAlignment: "center", verticalAlignment: "center", wrapText: true };
  ws.getRange(`A1:${end}${rows}`).format.borders = { insideHorizontal: { style: "thin", color: "#E5E7EB" }, bottom: { style: "thin", color: "#CBD5E1" } };
  ws.getRange(`A2:${end}${rows}`).format.verticalAlignment = "top";
  ws.freezePanes.freezeRows(1);
  widths.forEach((w, i) => ws.getRangeByIndexes(0, i, rows, 1).format.columnWidth = w);
}
styleTable(outline, 9, roleRows.length, [21, 7, 8, 55, 24, 12, 10, 12, 38]);
styleTable(local, 9, headingRows.length, [23, 8, 55, 15, 10, 12, 10, 12, 38]);
styleTable(table, 10, tableRows.length, [23, 10, 10, 10, 10, 22, 10, 55, 12, 38]);
outline.getRange(`H2:H${roleRows.length}`).dataValidation = { rule: { type: "list", values: ["待复核", "通过", "需修正"] } };
local.getRange(`H2:H${headingRows.length}`).dataValidation = { rule: { type: "list", values: ["待复核", "通过", "需修正"] } };
table.getRange(`I2:I${tableRows.length}`).dataValidation = { rule: { type: "list", values: ["待复核", "通过", "需修正"] } };
summary.getRange("A5:B13").format.borders = { preset: "all", style: "thin", color: "#D5DEE8" };
summary.getRange("D5:F9").format.borders = { preset: "all", style: "thin", color: "#D5DEE8" };
summary.getRange("A5:B5").format = { fill: "#1F4E78", font: { name: "Arial", bold: true, color: "#FFFFFF" } };
summary.getRange("D5:F5").format = { fill: "#1F4E78", font: { name: "Arial", bold: true, color: "#FFFFFF" } };
summary.getRange("A:A").format.columnWidth = 31; summary.getRange("B:B").format.columnWidth = 18;
summary.getRange("D:D").format.columnWidth = 25; summary.getRange("E:F").format.columnWidth = 12;
summary.getRange("A16:H18").format.wrapText = false; summary.getRange("A16:H18").format.rowHeight = 22;

wb.recalculate();
const inspect = await wb.inspect({ kind: "table", range: "结构总览!A1:H18", include: "values,formulas", tableMaxRows: 20, tableMaxCols: 8 });
console.log(inspect.ndjson);
const errors = await wb.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!", options: { useRegex: true, maxResults: 300 }, summary: "final formula error scan" });
console.log(errors.ndjson);
const preview = await wb.render({ sheetName: "结构总览", range: "A1:H18", scale: 1.5, format: "png" });
await fs.writeFile(path.join(outputDir, "structure_review_preview.png"), new Uint8Array(await preview.arrayBuffer()));
const blob = await SpreadsheetFile.exportXlsx(wb);
await blob.save(path.join(outputDir, "structure_review.xlsx"));
