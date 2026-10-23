"""High-precision, source-preserving structure recovery for Parser Final MVP."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any


CHAPTER_RE = re.compile(r"^第\s*[一二三四五六七八九十百零〇0-9]+\s*(章|节|部分)")
CN_TOP_RE = re.compile(r"^[一二三四五六七八九十百零〇]+[、．.]\s*\S+")
CN_PAREN_RE = re.compile(r"^[（(][一二三四五六七八九十百零〇]+[）)]\s*\S+")
ARABIC_TOP_RE = re.compile(r"^(\d+)[.、](?!\d)\s*\S+")
DEEP_RE = re.compile(r"^\d+(?:\.\d+)+[.、]?\s*\S+")
PURE_NUMBER_RE = re.compile(r"^\s*\d+[.、]\s*$")
BODY_PREFIXES = ("说明", "注：", "注:", "备注", "附件", "日期", "签章", "盖章")
STRUCTURAL_FAMILY_PATTERNS = {
    "CHAPTER": re.compile(r"^第\s*([一二三四五六七八九十百零〇0-9]+)\s*章"),
    "SECTION": re.compile(r"^第\s*([一二三四五六七八九十百零〇0-9]+)\s*节"),
    "PART": re.compile(r"^(?:第\s*)?([一二三四五六七八九十百零〇0-9]+)\s*部分"),
}


def active_blocks(page: dict[str, Any]) -> list[dict[str, Any]]:
    return [b for b in page.get("blocks", []) if "COVERED_BY_TABLE" not in b.get("flags", [])]


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def heading_decision(block: dict[str, Any], following: dict[str, Any] | None = None) -> dict[str, Any]:
    """Conservative heading decision. Body-protection gates run before positive rules."""
    if block.get("kind") != "text":
        return {"is_heading": False, "level": None, "score": 0, "reason": "NON_TEXT"}
    text = _clean(block.get("text")); label = block.get("parser_label")
    if not text:
        return {"is_heading": False, "level": None, "score": 0, "reason": "EMPTY"}
    if text in {"/。", "/", "。"} or any(text.startswith(x) for x in BODY_PREFIXES):
        return {"is_heading": False, "level": None, "score": 0, "reason": "BODY_PROTECTION_PREFIX"}
    if re.match(r"^[（(]\d+[）)]", text) or (text.startswith(("（", "(")) and not CN_PAREN_RE.match(text)):
        return {"is_heading": False, "level": None, "score": 0, "reason": "BODY_PROTECTION_PAREN_CONTENT"}
    if len(text) > 80 or text.endswith(("。", "；", ";")):
        return {"is_heading": False, "level": None, "score": 0, "reason": "BODY_PROTECTION_SENTENCE"}
    # A colon followed by a value is a field or clause, not a structural heading.
    if re.search(r"[：:]\s*\S{3,}", text):
        return {"is_heading": False, "level": None, "score": 0, "reason": "BODY_PROTECTION_FIELD"}
    if DEEP_RE.match(text):
        return {"is_heading": False, "level": None, "score": 0, "reason": "BODY_PROTECTION_DEEP_CLAUSE"}
    if CHAPTER_RE.match(text):
        return {"is_heading": True, "level": 1, "score": 10, "reason": "EXPLICIT_CHAPTER"}
    if CN_TOP_RE.match(text):
        return {"is_heading": True, "level": 2, "score": 9, "reason": "EXPLICIT_CN_TOP"}
    if CN_PAREN_RE.match(text):
        return {"is_heading": True, "level": 3, "score": 8, "reason": "EXPLICIT_CN_PAREN"}
    if ARABIC_TOP_RE.match(text):
        return {"is_heading": True, "level": 2, "score": 8, "reason": "EXPLICIT_ARABIC_TOP"}
    score = 0
    reasons: list[str] = []
    if label == "section_header": score += 1; reasons.append("PARSER_HEADER_WEAK")
    score += 1; reasons.append("INDEPENDENT_BLOCK")
    if len(text) <= 30: score += 1; reasons.append("SHORT")
    if not re.search(r"\d{4}|\d+\s*(元|%|日|天|平方米|万元)", text): score += 1; reasons.append("NO_CONCRETE_VALUE")
    if not re.search(r"[，,；;。]", text): score += 1; reasons.append("NO_SENTENCE_PUNCT")
    if following and len(_clean(following.get("text"))) >= 35: score += 1; reasons.append("FOLLOWED_BY_BODY")
    title_suffix = bool(re.search(r"(格式|公告|邀请书|投标须知|评分标准|合同|委托书|证明书|补正表)$", text))
    allowed = label == "section_header" or title_suffix
    return {"is_heading": bool(allowed and score >= 4), "level": 2 if allowed and score >= 4 else None,
            "score": score, "reason": "+".join(reasons) if reasons else "NO_HEADING_SIGNAL"}


class _Cells(HTMLParser):
    def __init__(self) -> None:
        super().__init__(); self.cells: list[str] = []; self._buf: list[str] | None = None
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"td", "th"}: self._buf = []
    def handle_data(self, data: str) -> None:
        if self._buf is not None: self._buf.append(data)
    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"td", "th"} and self._buf is not None:
            self.cells.append(_clean("".join(self._buf))); self._buf = None


def detect_toc_page(page: dict[str, Any]) -> bool:
    blocks = active_blocks(page); combined = " ".join(_clean(b.get("text")) for b in blocks)
    cells: list[str] = []
    for block in blocks:
        if block.get("kind") == "table" and block.get("table_html"):
            parser = _Cells(); parser.feed(str(block["table_html"])); cells.extend(parser.cells)
    material = " ".join(cells) or combined
    chapter_hits = len(re.findall(r"第\s*[一二三四五六七八九十百零〇0-9]+\s*[章节]", material))
    page_hits = len(re.findall(r"(?:\.{3,}|…{2,})\s*\d+|\s\d{1,3}(?=\s|$)", material))
    return "目录" in material or (
        any(b.get("parser_label") == "document_index" for b in blocks) and (chapter_hits + page_hits >= 2))


def _cn_integer(value: str) -> int | None:
    if value.isdigit(): return int(value)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十": return 10
    if "十" in value:
        left, right = value.split("十", 1)
        if left not in {"", *digits} or right not in {"", *digits}: return None
        return (digits.get(left, 1) * 10) + digits.get(right, 0)
    if value in digits: return digits[value]
    if value == "百": return 100
    return None


def _structural_family(text: str) -> tuple[str, int] | None:
    cleaned = _clean(text)
    for family, pattern in STRUCTURAL_FAMILY_PATTERNS.items():
        match = pattern.match(cleaned)
        if match:
            number = _cn_integer(match.group(1))
            return (family, number) if number is not None else None
    return None


def detect_structural_enumeration_runs(page: dict[str, Any],
                                       order: list[str] | None = None) -> list[dict[str, Any]]:
    """Find body-introduced structural-name lists without using document-specific IDs."""
    if page.get("page_type") == "toc" or detect_toc_page(page): return []
    blocks = active_blocks(page); by_id = {b["block_id"]: b for b in blocks}
    ordered = [by_id[x] for x in (order or [b["block_id"] for b in blocks])]
    runs: list[dict[str, Any]] = []; i = 1
    while i < len(ordered):
        intro = ordered[i - 1]
        intro_text = _clean(intro.get("text"))
        intro_decision = heading_decision(intro, ordered[i] if i < len(ordered) else None)
        if intro.get("kind") != "text" or intro_decision["is_heading"] or not intro_text.endswith(("：", ":")):
            i += 1; continue
        family_data = _structural_family(ordered[i].get("text", "")) if ordered[i].get("kind") == "text" else None
        if not family_data:
            i += 1; continue
        family = family_data[0]; members: list[dict[str, Any]] = []; numbers: list[int] = []; j = i
        while j < len(ordered) and ordered[j].get("kind") == "text":
            item = _structural_family(ordered[j].get("text", ""))
            base = heading_decision(ordered[j], ordered[j + 1] if j + 1 < len(ordered) else None)
            if not item or item[0] != family or not base["is_heading"]: break
            members.append(ordered[j]); numbers.append(item[1]); j += 1
        sequential = len(numbers) >= 3 and all(numbers[k] == numbers[k - 1] + 1 for k in range(1, len(numbers)))
        if sequential:
            reason = ["PREVIOUS_TEXT_LIST_INTRO_COLON", f"CONSECUTIVE_{family}_RUN", "SEQUENTIAL_NUMBERS"]
            if re.search(r"包括下列|如下|包括|组成|分为", intro_text): reason.append("LIST_INTRO_LEXICAL_SIGNAL")
            runs.append({"page_no": int(page["page_no"]), "family": family,
                         "intro_source_id": intro["block_id"], "intro_text": intro.get("text", ""),
                         "run_source_ids": [b["block_id"] for b in members],
                         "run_texts": [b.get("text", "") for b in members],
                         "sequence_numbers": numbers, "reason": reason})
            i = j
        else: i += 1
    return runs


def _same_line(a: dict[str, Any], b: dict[str, Any]) -> bool:
    ba, bb = a.get("bbox_norm") or [], b.get("bbox_norm") or []
    return len(ba) == 4 and len(bb) == 4 and abs((ba[1]+ba[3])/2-(bb[1]+bb[3])/2) <= .015


def build_final_page(page: dict[str, Any], repaired_order: list[str] | None = None,
                     table_repairs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    blocks = active_blocks(page); by_id = {b["block_id"]: b for b in blocks}
    order = repaired_order or [b["block_id"] for b in blocks]
    merge_at: dict[str, tuple[str, str]] = {}; merge_members: set[str] = set()
    for pos, bid in enumerate(order):
        b = by_id[bid]
        if not PURE_NUMBER_RE.match(_clean(b.get("text"))): continue
        for candidate in (order[pos-1:pos] + order[pos+1:pos+2]):
            cb = by_id[candidate]
            if cb.get("kind") == "text" and not PURE_NUMBER_RE.match(_clean(cb.get("text"))) and _same_line(b, cb):
                first = order[min(pos, order.index(candidate))]
                merge_at[first] = (bid, candidate); merge_members.update((bid, candidate)); break
    elements: list[dict[str, Any]] = []; consumed: set[str] = set(); i = 0
    while i < len(order):
        bid = order[i]; block = by_id[bid]
        if bid in merge_at:
            number_id, title_id = merge_at[bid]; number = _clean(by_id[number_id].get("text")); title = _clean(by_id[title_id].get("text"))
            elements.append({"element_id": number_id, "source_ids": [number_id, title_id], "kind": "text",
                             "semantic_type": "heading", "heading_level": 2,
                             "text": f"{number} {title}", "decision": "CODE_MERGE_ISOLATED_NUMBER_TITLE"})
            consumed.update((number_id, title_id)); i += 1; continue
        if bid in consumed or bid in merge_members: i += 1; continue
        following = by_id.get(order[i+1]) if i + 1 < len(order) else None
        if block.get("kind") == "table":
            element = {"element_id": bid, "source_ids": [bid], "kind": "table",
                       "semantic_type": "table", "text": block.get("text", ""),
                       "table_html": block.get("table_html", ""), "decision": "SOURCE_TABLE"}
        else:
            decision = heading_decision(block, following)
            element = {"element_id": bid, "source_ids": [bid], "kind": "text",
                       "semantic_type": "heading" if decision["is_heading"] else "text",
                       "heading_level": decision["level"], "text": block.get("text", ""),
                       "decision": decision["reason"], "heading_score": decision["score"]}
        elements.append(element); consumed.add(bid); i += 1
    enumeration_runs = detect_structural_enumeration_runs(page, order)
    demoted = {source_id for run in enumeration_runs for source_id in run["run_source_ids"]}
    for element in elements:
        if len(element["source_ids"]) == 1 and element["source_ids"][0] in demoted:
            element["semantic_type"] = "text"; element["heading_level"] = None
            element["decision"] = "BODY_ENUMERATION_LIST"
    merged = any(e.get("decision") == "CODE_MERGE_ISOLATED_NUMBER_TITLE" for e in elements)
    return {"schema_version": "PF1-final-page-v1", "page_no": int(page["page_no"]),
            "page_type": "toc" if detect_toc_page(page) else "content",
            "order_source": "VLM_REPAIR" if repaired_order else ("CODE_MERGE" if merged else "A1_DEFAULT"),
            "ordered_elements": elements, "table_repairs": table_repairs or [],
            "enumeration_runs": enumeration_runs}


def source_text_coverage(page: dict[str, Any], final: dict[str, Any]) -> dict[str, Any]:
    expected = [b["block_id"] for b in active_blocks(page)]
    actual = [x for e in final["ordered_elements"] for x in e["source_ids"]]
    return {"page_no": page["page_no"], "expected": len(expected), "covered": len(set(actual)),
            "missing": sorted(set(expected)-set(actual)), "duplicate": sorted(x for x in set(actual) if actual.count(x)>1),
            "unknown": sorted(set(actual)-set(expected)), "coverage": len(set(actual)&set(expected))/len(expected) if expected else 1.0}
