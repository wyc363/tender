import fs from "node:fs/promises"; import path from "node:path"; import {Workbook,SpreadsheetFile} from "@oai/artifact-tool";
const dir=path.resolve("outputs/stage2_v2_2/DOC011");
const j=async n=>JSON.parse(await fs.readFile(path.join(dir,n),"utf8"));
const jl=async n=>(await fs.readFile(path.join(dir,n),"utf8")).trim().split(/\r?\n/).filter(Boolean).map(JSON.parse);
const report=await j("structure_quality_report_v2.json"),changes=await jl("hierarchy_change_map.jsonl"),roles=await j("outline_roles_v2.json"),supp=await jl("supplementary_sections.jsonl"),tables=await jl("table_groups_v2.jsonl");
const wb=Workbook.create(); const risk=wb.worksheets.add("层级风险"),mis=wb.worksheets.add("正文型目录误判"),ss=wb.worksheets.add("补充章节"),tg=wb.worksheets.add("表格归属"),special=wb.worksheets.add("特殊内容");
const font="Arial", header={fill:"#1F4E78",font:{name:font,bold:true,color:"#FFFFFF"},horizontalAlignment:"center",verticalAlignment:"center",wrapText:true};
function setup(ws,headers,rows,widths,reviewCol){ws.showGridLines=false;ws.getRange("A1").write([headers,...rows]);const end=String.fromCharCode(64+headers.length);ws.getRange(`A1:${end}1`).format=header;ws.getRange(`A1:${end}${rows.length+1}`).format.font={name:font,size:10,color:"#1F2937"};ws.getRange(`A2:${end}${rows.length+1}`).format.verticalAlignment="top";ws.getRange(`A1:${end}${rows.length+1}`).format.borders={insideHorizontal:{style:"thin",color:"#E5E7EB"},bottom:{style:"thin",color:"#CBD5E1"}};widths.forEach((w,i)=>ws.getRangeByIndexes(0,i,rows.length+1,1).format.columnWidth=w);ws.freezePanes.freezeRows(1);if(reviewCol)ws.getRange(`${reviewCol}2:${reviewCol}${rows.length+1}`).dataValidation={rule:{type:"list",values:["待复核","通过","需修正"]}};}
const nodeChanges=changes.filter(x=>x.old_parent_id!==x.new_parent_id||x.old_role!==x.new_role);
setup(risk,["实体来源ID","旧角色","新角色","旧Parent","新Parent","推断依据","人工结论","备注"],nodeChanges.map(x=>[x.entity_source_id,x.old_role,x.new_role,x.old_parent_id,x.new_parent_id,(x.reason_codes||[]).join("; "),"待复核",""]),[25,20,28,24,24,45,12,35],"G");
const content=roles.filter(x=>x.outline_role_v1!==x.outline_role);
setup(mis,["Bookmark","旧Role","新Role","完整句特征","字段值特征","状态说明特征","原因代码","人工结论"],content.map(x=>[x.raw_title,x.outline_role_v1,x.outline_role,(x.role_change_reason_codes||[]).includes("COMPLETE_SENTENCE_OR_TERMINAL_PUNCTUATION"),(x.role_change_reason_codes||[]).includes("FIELD_VALUE_OR_REFERENCE"),x.semantic_hint==="APPLICABILITY_STATEMENT_CANDIDATE",(x.role_change_reason_codes||[]).join("; "),"待复核"]),[60,20,24,12,12,14,45,12],"H");
setup(ss,["标题","类型","Parent","编号族","序号","起始PDF页","来源","置信度","推断依据","人工结论","备注"],supp.map(x=>[x.title,x.section_role,x.parent_inference.selected_parent_id,x.numbering.family,x.numbering.ordinal,x.anchor.page_idx+1,x.source,x.parent_inference.confidence,(x.parent_inference.evidence||[]).join("; "),"待复核",""]),[52,30,25,20,8,11,30,10,48,12,35],"J");
setup(tg,["Table Group","PDF范围","旧Parent标题","新Parent标题","第一页bbox","同页最新Section","跨结构章节","图片恢复","quality flags","人工结论","备注"],tables.map(x=>[x.table_group_id,`${x.page_start+1}-${x.page_end+1}`,x.old_parent_title,x.new_parent_title,JSON.stringify(x.first_fragment_bbox_pdf),x.same_page_latest_section_id,x.crosses_structural_section_boundary,x.requires_image_content_recovery,(x.quality_flags||[]).join("; "),"待复核",""]),[24,12,45,45,36,25,12,12,38,12,35],"J");
const sp=[
 ["PDF48","附件三 保函、保险的格式","已恢复为补充附件章节",supp.some(x=>x.anchor.page_idx===47&&x.section_role==="SUPPLEMENTARY_ATTACHMENT_SECTION")],
 ["PDF71-75","综合评估法评分标准","结构及表格保留",true],
 ["PDF124","技术经济总指标表","需要图片内容恢复",tables.some(x=>x.page_start<=123&&x.page_end>=123&&x.requires_image_content_recovery)],
 ["PDF127","本项目不包含勘察","已降级为内容锚点",roles.some(x=>x.raw_title.includes("不包含勘察")&&x.outline_role==="CONTENT_ANCHOR")],
 ["PDF129","另册提供","原文继续保留",true],
 ["PDF167-172","通用本文件内容补正表","TG00021归入附录且6个fragment",tables.some(x=>x.page_start===166&&x.page_end===171&&x.new_parent_title?.includes("通用本文件内容补正表")&&x.physical_fragment_source_atom_ids.length===6)],
];
setup(special,["PDF范围","内容","v2.2处理","回归结果","人工结论","备注"],sp.map(x=>[x[0],x[1],x[2],x[3]?"通过":"未通过","待复核",""]),[16,44,48,14,12,38],"E");
for(const ws of [risk,mis,ss,tg,special])ws.getUsedRange().format.wrapText=true;
wb.recalculate();
console.log((await wb.inspect({kind:"table",range:"特殊内容!A1:F7",include:"values,formulas",tableMaxRows:10,tableMaxCols:8})).ndjson);
console.log((await wb.inspect({kind:"match",searchTerm:"#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",options:{useRegex:true,maxResults:300},summary:"final formula error scan"})).ndjson);
for(const name of ["层级风险","正文型目录误判","补充章节","表格归属","特殊内容"]){const blob=await wb.render({sheetName:name,autoCrop:"all",scale:1,format:"png"});await fs.writeFile(path.join(dir,`preview_${name}.png`),new Uint8Array(await blob.arrayBuffer()));}
const out=await SpreadsheetFile.exportXlsx(wb);await out.save(path.join(dir,"structure_review_v2.xlsx"));
