import fs from "node:fs/promises";
import path from "node:path";
import {Workbook,SpreadsheetFile} from "@oai/artifact-tool";
const out=path.resolve(process.argv[2]||"");if(!process.argv[2])throw new Error("usage: node build_content_unit_review_v31.mjs <output-dir>");
const jl=async n=>(await fs.readFile(path.join(out,n),"utf8")).split(/\r?\n/).filter(Boolean).map(JSON.parse);
const report=JSON.parse(await fs.readFile(path.join(out,"content_unit_quality_report_v3_1.json"),"utf8"));
const forms=await jl("form_instruction_composition_audit.jsonl"),cross=await jl("cross_page_text_continuation_audit.jsonl"),inline=await jl("inline_boundary_audit.jsonl"),recovery=await jl("selective_table_recovery_audit.jsonl"),units=await jl("content_units_v3_1.jsonl");
const wb=Workbook.create(),font="Arial",clip=(x,n=2500)=>String(x??"").slice(0,n);
function sheet(name,headers,rows,widths){const s=wb.worksheets.add(name);s.showGridLines=false;s.getRangeByIndexes(0,0,1,headers.length).values=[headers];if(rows.length)s.getRangeByIndexes(1,0,rows.length,headers.length).values=rows;const used=s.getUsedRange();used.format.font={name:font,size:10};used.format.verticalAlignment="top";used.format.wrapText=true;s.getRangeByIndexes(0,0,1,headers.length).format={fill:"#17365D",font:{name:font,size:10,bold:true,color:"#FFFFFF"},horizontalAlignment:"center",verticalAlignment:"center",wrapText:true,borders:{preset:"all",style:"thin",color:"#D9E1E8"}};widths.forEach((w,i)=>s.getRangeByIndexes(0,i,Math.max(1,used.rowCount),1).format.columnWidth=w);s.freezePanes.freezeRows(1);return s}
const overview=sheet("Overview",["指标","结果"],[
 ["Stage2-v3输入Unit",report.metrics.stage2_v3_input_unit_count],["Stage2-v3.1最终Unit",report.metrics.content_unit_count],["总验收",report.status_zh],
 ["Bare Form Instruction",report.metrics.bare_form_instruction_unit_count],["Form Instruction合并",report.metrics.form_instruction_composed_count],
 ["跨页正文孤儿",report.metrics.orphan_continuation_fragment_count],["跨页正文合并",report.metrics.cross_page_text_continuation_merged_count],
 ["内部多标题残留",report.metrics.unit_contains_multiple_high_confidence_internal_heading_count],["Inline Heading拆分",report.metrics.inline_heading_split_count],
 ["Recovery Required",report.metrics.recovery_required_total_count],["高优先级未恢复",report.metrics.recovery_required_high_priority_content_count],
 ["Selective Recovery成功",report.metrics.selective_table_recovery_success_count],["G19",report.gates.find(x=>x.gate_id==="G19")?.status_zh]
],[34,18]);overview.getRange("A1:B1").format.fill="#17365D";
sheet("Bare Form Instruction",["Lead Unit","吸收Unit","结果Unit","PDF开始","PDF结束","人工备注"],forms.map(x=>[x.lead_unit_id,(x.absorbed_unit_ids||[]).join("\n"),x.result_unit_id,x.page_start+1,x.page_end+1,""]),[22,36,22,12,12,28]);
sheet("Cross-page Text Continuation",["前页Unit","续接Unit","结果Unit","PDF开始","PDF结束","人工备注"],cross.map(x=>[x.previous_unit_id,x.continuation_unit_id,x.result_unit_id,x.page_start+1,x.page_end+1,""]),[22,22,22,12,12,32]);
sheet("Internal Heading Split",["原Unit","新Unit","边界证据","人工备注"],inline.map(x=>[x.old_unit_id,x.new_unit_ids.join("\n"),clip(JSON.stringify(x.boundaries)),""]),[22,36,80,32]);
const remaining=units.filter(x=>x.reconstruction_status==="RECOVERY_REQUIRED");sheet("Recovery Required",["Unit","Table Group","优先级","PDF","失败原因","Quality Flags","人工备注"],remaining.length?remaining.map(x=>[x.unit_id,x.source_table_group_ids.join("\n"),x.recovery_priority,x.page_start+1,x.recovery_failure_reason||"",x.quality_flags.join("\n"),""]):[["无未恢复Unit","", "", "", "", "", ""]],[22,22,14,10,48,36,30]);
sheet("Selective Table Recovery",["Unit","Table Group","优先级","成功","方法","置信度","证据","失败原因","人工备注"],recovery.map(x=>[x.unit_id,x.table_group_id,x.priority,x.success,x.recovery_method||"",x.confidence,clip(JSON.stringify(x.evidence)),x.failure_reason||"",""]),[22,22,14,10,22,12,70,44,30]);
wb.recalculate();console.log((await wb.inspect({kind:"table",sheetId:"Overview",range:"A1:B14",include:"values,formulas",tableMaxRows:20,tableMaxCols:4})).ndjson);console.log((await wb.inspect({kind:"match",searchTerm:"#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",options:{useRegex:true,maxResults:100},summary:"formula errors"})).ndjson);
for(const name of ["Overview","Bare Form Instruction","Cross-page Text Continuation","Internal Heading Split","Recovery Required","Selective Table Recovery"]){const b=await wb.render({sheetName:name,range:name==="Overview"?"A1:B14":"A1:F12",scale:1,format:"png"});await b.arrayBuffer()}
const blob=await SpreadsheetFile.exportXlsx(wb);await blob.save(path.join(out,"content_unit_review_v3_1.xlsx"));console.log(JSON.stringify({output:path.join(out,"content_unit_review_v3_1.xlsx"),sheets:6}));
