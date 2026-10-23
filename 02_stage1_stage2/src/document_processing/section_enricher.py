from __future__ import annotations
def run(units,section_map):
    sections=section_map.get("sections",[]) if section_map else []
    for u in units:
        matches=[s for s in sections if s["pdf_page_start"]<=u.pdf_page_start<=s["pdf_page_end"]]
        matches.sort(key=lambda s:s["pdf_page_end"]-s["pdf_page_start"])
        if matches: u.section_path=[matches[0]["title"]]
        else: u.quality_flags.append("SECTION_PATH_UNKNOWN")
    return units
