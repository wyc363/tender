import fs from "node:fs/promises";
import path from "node:path";
import { Workbook, SpreadsheetFile } from "@oai/artifact-tool";

const root=path.resolve(import.meta.dirname,"..");
const out=path.join(root,"outputs","stage2_v3_content_units","DOC011");
const lines=(await fs.readFile(path.join(out,"content_units.jsonl"),"utf8")).trim().split(/\r?\n/).filter(Boolean);
const units=lines.map(JSON.parse);
const report=JSON.parse(await fs.readFile(path.join(out,"content_unit_quality_report.json"),"utf8"));
const clip=(v,n=3000)=>String(v??"").slice(0,n);
const wb=Workbook.create();
const overview=wb.worksheets.add("Overview");
const unitSheet=wb.worksheets.add("Content Units");
const tableSheet=wb.worksheets.add("Table Rows");
const formSheet=wb.worksheets.add("Forms");
const gateSheet=wb.worksheets.add("Gates");

overview.getRange("A2:D2").values=[["DOC011 Stage2-v3 Content Unit Review","","",""]];
overview.getRange("A4:B4").values=[["指标","结果"]];
const metrics=[
 ["总验收",report.status_zh],["Content Unit",report.metrics.content_unit_count],
 ...Object.entries(report.metrics.unit_type_counts),["Eligible Block未归属",report.metrics.eligible_content_block_unassigned_count],
 ["Block多重Primary归属",report.metrics.eligible_content_block_multiple_primary_unit_count],
 ["跨页Table Group",report.metrics.cross_page_table_group_count],["跨页逻辑行",report.metrics.cross_page_logical_row_count],
 ["Form Instruction",report.metrics.form_instruction_unit_count],["Form Body",report.metrics.form_body_unit_count]
];
overview.getRangeByIndexes(4,0,metrics.length,2).values=metrics;

const unitHeaders=["unit_id","unit_type","section_path","page_start_pdf","page_end_pdf","clause_ref","local_heading_context","text","primary_block_ids","table_group_ids","status","quality_flags","human_note"];
const unitRows=units.map(u=>[u.unit_id,u.unit_type,u.section_path.join(" / "),u.page_start+1,u.page_end+1,u.clause_ref_normalized||"",u.local_heading_context.join(" / "),clip(u.text),u.primary_source_block_ids.join("\n"),u.source_table_group_ids.join("\n"),u.reconstruction_status,u.quality_flags.join(" / "),""]);
unitSheet.getRangeByIndexes(0,0,1,unitHeaders.length).values=[unitHeaders]; unitSheet.getRangeByIndexes(1,0,unitRows.length,unitHeaders.length).values=unitRows;

const tableHeaders=["unit_id","table_group_id","section_path","page_start_pdf","page_end_pdf","cross_page","continuation","headers","parent_row_context","cells/text","provenance","status","quality_flags","human_note"];
const tableUnits=units.filter(u=>u.unit_type==="TABLE_ROW_GROUP_UNIT");
const tableRows=tableUnits.map(u=>[u.unit_id,u.table_context.table_group_id,u.section_path.join(" / "),u.page_start+1,u.page_end+1,u.table_context.cross_page,u.table_context.continuation,u.table_context.column_headers.join(" / "),u.table_context.parent_row_context.join(" / "),clip(u.text),clip(JSON.stringify(u.provenance),2000),u.reconstruction_status,u.quality_flags.join(" / "),""]);
tableSheet.getRangeByIndexes(0,0,1,tableHeaders.length).values=[tableHeaders]; tableSheet.getRangeByIndexes(1,0,tableRows.length,tableHeaders.length).values=tableRows;

const formHeaders=["unit_id","unit_type","section_path","page_pdf","text","status","human_note"];
const formRows=units.filter(u=>u.unit_type.startsWith("FORM_")).map(u=>[u.unit_id,u.unit_type,u.section_path.join(" / "),u.page_start+1,clip(u.text),u.reconstruction_status,""]);
formSheet.getRangeByIndexes(0,0,1,formHeaders.length).values=[formHeaders]; formSheet.getRangeByIndexes(1,0,formRows.length,formHeaders.length).values=formRows;

const gateHeaders=["gate_id","名称","结果","关联指标"];
const gateRows=report.gates.map(g=>[g.gate_id,g.name_zh,g.status_zh,(g.metrics||[]).join("\n")]);
gateSheet.getRangeByIndexes(0,0,1,4).values=[gateHeaders]; gateSheet.getRangeByIndexes(1,0,gateRows.length,4).values=gateRows;

for(const [sheet,widths] of [[overview,[34,18]],[unitSheet,[20,20,46,12,12,14,28,80,26,20,18,28,24]],[tableSheet,[20,20,46,12,12,12,12,34,34,80,55,18,28,24]],[formSheet,[20,22,48,12,90,18,24]],[gateSheet,[14,34,14,55]]]){
 sheet.showGridLines=false; sheet.freezePanes.freezeRows(sheet===overview?4:1);
 const used=sheet.getUsedRange(); used.format.font={name:"Arial",size:10}; used.format.verticalAlignment="top";
 const header=sheet===overview?sheet.getRange("A4:B4"):sheet.getRangeByIndexes(0,0,1,widths.length);
 header.format={fill:"#17365D",font:{name:"Arial",size:10,bold:true,color:"#FFFFFF"},horizontalAlignment:"center",verticalAlignment:"center",wrapText:true,borders:{preset:"all",style:"thin",color:"#D9E1E8"}};
 widths.forEach((w,i)=>sheet.getRangeByIndexes(0,i,Math.max(1,used.rowCount),1).format.columnWidth=w);
 used.format.wrapText=true;
 }
overview.getRange("A2:D2").format.font={name:"Arial",size:14,bold:true,color:"#17365D"};
wb.recalculate();
console.log((await wb.inspect({kind:"table",sheetId:"Overview",range:"A2:B18",include:"values,formulas",tableMaxRows:20,tableMaxCols:4})).ndjson);
console.log((await wb.inspect({kind:"match",searchTerm:"#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",options:{useRegex:true,maxResults:100},summary:"formula errors"})).ndjson);
for(const name of ["Overview","Content Units","Table Rows","Forms","Gates"]){ const img=await wb.render({sheetName:name,range:name==="Overview"?"A1:D18":"A1:H12",scale:1,format:"png"}); await img.arrayBuffer(); }
const blob=await SpreadsheetFile.exportXlsx(wb); await blob.save(path.join(out,"content_unit_review.xlsx"));
console.log(JSON.stringify({output:path.join(out,"content_unit_review.xlsx"),units:units.length,tableRows:tableUnits.length,forms:formRows.length}));
