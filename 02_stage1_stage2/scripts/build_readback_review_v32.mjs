import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const documentId = process.argv[2];
if (!documentId) throw new Error("Usage: node build_readback_review_v32.mjs <document_id>");
const outputDir = path.join(root, "outputs", "stage2_v3_2_readback", documentId);
const readJson = async name => JSON.parse(await fs.readFile(path.join(outputDir, name), "utf8"));
const readJsonl = async name => (await fs.readFile(path.join(outputDir, name), "utf8")).split(/\r?\n/).filter(Boolean).map(JSON.parse);
const report = await readJson("readback_quality_report_v3_2.json");
const events = await readJsonl("canonical_content_events_v3_2.jsonl");
const front = await readJsonl("document_front_matter_v3_2.jsonl");
const tableAudit = await readJsonl("table_text_consumption_audit_v3_2.jsonl");
const traces = await readJsonl("readback_mismatch_trace_v3_2.jsonl");

const wb = Workbook.create();
const titleFill = "#1F4E78", headerFill = "#D9EAF7", font = "Arial";
function scalar(value) {
  if (value === null || value === undefined) return "";
  return typeof value === "object" ? JSON.stringify(value) : value;
}
function addSheet(name, rows, preferredKeys) {
  const ws = wb.worksheets.add(name); ws.showGridLines = false;
  const keys = preferredKeys || [...new Set(rows.flatMap(row => Object.keys(row)))];
  ws.getRange("A1").values = [[name]]; ws.getRange(`A1:${String.fromCharCode(64 + Math.min(keys.length, 26))}1`).format.font = {name:font,bold:true,size:14,color:"#1F1F1F"};
  if (keys.length) {
    ws.getRangeByIndexes(2,0,1,keys.length).values=[keys];
    ws.getRangeByIndexes(2,0,1,keys.length).format={fill:headerFill,font:{name:font,bold:true,color:"#1F1F1F"},wrapText:true,borders:{preset:"bottom",style:"thin",color:"#9EADBA"}};
    if (rows.length) ws.getRangeByIndexes(3,0,rows.length,keys.length).values=rows.map(row=>keys.map(k=>scalar(row[k])));
    const used=ws.getRangeByIndexes(2,0,Math.max(1,rows.length+1),keys.length); used.format.font={name:font,size:10}; used.format.verticalAlignment="top";
    used.format.autofitColumns();
    for(let c=0;c<keys.length;c++) {
      const wide = /text|preview|flags|source|provenance|Value|cells|front_matter/i.test(keys[c]);
      ws.getRangeByIndexes(2,c,Math.max(1,rows.length+1),1).format.columnWidthPx = wide ? 280 : Math.min(220,Math.max(110,keys[c].length*8));
    }
    if (name === "Summary") { ws.getRangeByIndexes(0,0,rows.length+3,1).format.columnWidthPx=340; ws.getRangeByIndexes(0,1,rows.length+3,1).format.columnWidthPx=360; }
    ws.getRangeByIndexes(3,0,Math.max(1,rows.length),keys.length).format.wrapText=true; ws.freezePanes.freezeRows(3);
  }
  ws.tabColor = name === "Summary" ? titleFill : undefined;
  return ws;
}
const summaryRows = Object.entries(report.metrics).map(([Metric,Value])=>({Metric,Value:scalar(Value)}));
addSheet("Summary",summaryRows,["Metric","Value"]);
addSheet("Event Order",events.map(e=>({event_index:e.event_index,event_type:e.event_type,event_id:e.event_id,page:e.canonical_position?.[0],y:e.canonical_position?.[1],reading_order:e.canonical_position?.[2],intra_source_order:e.canonical_position?.[3],section_id:e.section_id,unit_id:e.unit_id,unit_type:e.unit_type,text_preview:(e.text||e.section_title||"").slice(0,180),quality_flags:e.quality_flags})),null);
addSheet("Section Anchor Order",events.filter(e=>e.event_type==="SECTION_ANCHOR_EVENT"),null);
addSheet("Unrepresented Source",[],["source_id","reason"]);
addSheet("Duplicate Render",[],["source_id","render_count"]);
addSheet("Front Matter",front,null);
addSheet("Cross-page Tables",tableAudit,null);
addSheet("Known Regressions",traces,null);
addSheet("Upstream Defects",traces.filter(x=>x.failure_class?.startsWith("UPSTREAM_")),null);
wb.recalculate();
const check = await wb.inspect({kind:"table",range:"Summary!A1:B30",include:"values,formulas",tableMaxRows:30,tableMaxCols:2});
console.log(check.ndjson);
const errors = await wb.inspect({kind:"match",searchTerm:"#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",options:{useRegex:true,maxResults:50},summary:"final formula error scan"});
console.log(errors.ndjson);
const preview = await wb.render({sheetName:"Summary",autoCrop:"all",scale:1,format:"png"});
await fs.writeFile(path.join(outputDir,"readback_review_v3_2_preview.png"),new Uint8Array(await preview.arrayBuffer()));
const output = await SpreadsheetFile.exportXlsx(wb);
await output.save(path.join(outputDir,"readback_review_v3_2.xlsx"));
