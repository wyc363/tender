from __future__ import annotations

import html,re
from typing import Any


def _text(value: Any) -> str:
    return "\n".join(re.sub(r"[ \t\u3000]+"," ",x).strip() for x in str(value or "").splitlines()).strip()


def render(events: list[dict[str, Any]], document_id: str) -> tuple[str, list[str]]:
    lines=[f"# {document_id}",""]; rendered=[]; i=0
    while i<len(events):
        event=events[i]; rendered.append(event["event_id"])
        if event["event_type"]=="FRONT_MATTER_EVENT":
            fm=event["front_matter"]; labels={"COVER_META":"封面","DOCUMENT_INSTRUCTION":"使用说明","TOC":"目录"}
            lines += [f"## {labels.get(fm['front_matter_type'],fm['front_matter_type'])}","",_text(fm["text"]),""]
        elif event["event_type"]=="SECTION_ANCHOR_EVENT":
            level=min(6,max(2,int(event.get("section_depth") or 1)+1));lines += ["#"*level+" "+_text(event["section_title"]),""]
        elif event["event_type"]=="CONTENT_UNIT_EVENT": lines += [_text(event["text"]),""]
        else:
            group=event["logical_row"]["table_group_id"] if event.get("logical_row") else None; group_events=[]
            while i<len(events) and events[i]["event_type"]=="TABLE_ROW_EVENT" and (events[i].get("logical_row") or {}).get("table_group_id")==group:
                group_events.append(events[i]);
                if events[i]["event_id"] not in rendered: rendered.append(events[i]["event_id"])
                i+=1
            headers=(group_events[0]["logical_row"].get("headers") or []) if group_events else []
            if headers:
                lines += ["<table>","  <thead><tr>"+"".join(f"<th>{html.escape(str(h))}</th>" for h in headers)+"</tr></thead>","  <tbody>"]
                for ge in group_events:
                    cells=ge["logical_row"]["render_cells"]; row="    <tr>"
                    for cell in cells:
                        span=f' colspan="{cell["colspan"]}"' if cell["colspan"]>1 else ""
                        row += f"<td{span}>{html.escape(_text(cell['value'])).replace(chr(10),'<br>')}</td>"
                    lines.append(row+"</tr>")
                lines += ["  </tbody>","</table>",""]
            else:
                lines += ["【表格】",""]+[ _text(x["text"]) for x in group_events]+[""]
            continue
        i+=1
    return "\n".join(lines).rstrip()+"\n",rendered

