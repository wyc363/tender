import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";
const path = process.argv[2];
const previewDir = process.argv[3];
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(path));
const check = await workbook.inspect({kind:"table",range:"统计汇总!A1:C12",include:"values,formulas",tableMaxRows:12,tableMaxCols:3,maxChars:5000});
console.log(check.ndjson);
const errors = await workbook.inspect({kind:"match",searchTerm:"#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",options:{useRegex:true,maxResults:100},summary:"formula errors"});
console.log(errors.ndjson);
await fs.mkdir(previewDir,{recursive:true});
const ranges={"DOC011_All":"A1:AA12","DOC012_All":"A1:AA12","重点审核":"A1:AB12","UNCERTAIN专项":"A1:J12","统计汇总":"A1:C12","分层抽查":"A1:AB12"};
for (const [sheetName,range] of Object.entries(ranges)) {
  const blob=await workbook.render({sheetName,range,scale:1,format:"png"});
  await fs.writeFile(`${previewDir}/${sheetName}.png`,new Uint8Array(await blob.arrayBuffer()));
}
