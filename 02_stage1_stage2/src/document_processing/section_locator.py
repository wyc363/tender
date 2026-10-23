from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any


CHINESE_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
CHAPTER_RE = re.compile(r"第\s*([一二三四五六七八九十百0-9]+)\s*章")
SECTION_RE = re.compile(r"第\s*([一二三四五六七八九十百0-9]+)\s*节")
TOC_DOTS_RE = re.compile(r"(?:\.{3,}|…{2,}|·{3,}|-{4,}|—{3,})\s*\d+\s*$")
TRAILING_PAGE_RE = re.compile(r"\s\d{1,4}\s*$")

ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ANNOUNCEMENT": ("招标公告", "投标邀请书"),
    "BIDDER_NOTICE_CHAPTER": ("投标人须知", "投标须知"),
    "BIDDER_NOTICE": ("投标人须知", "投标须知"),
    "QUALIFICATION": ("资格审查办法和标准", "资格审查办法", "资格审查"),
    "EVALUATION": ("评标办法和标准", "评标办法"),
    "DETERMINATION": ("评定分离定标办法", "定标办法", "定标规则", "定标因素"),
    "AWARD_SELECTION": ("择优因素", "中标候选人确定"),
    "CONTRACT": ("合同条款及格式", "合同条件及格式", "合同条款", "承包合同"),
    "TECH_SPEC": ("技术标准和要求", "技术标准及要求", "技术要求"),
    "FORM_TEMPLATE": ("投标文件格式", "投标文件格式及要求"),
}
BODY_REFERENCE_RE = re.compile(r"(中要求|中规定|的规定|详见|参见|见第|按照|应当|应|须|其中|上述|如下)")


@dataclass(frozen=True)
class Candidate:
    role: str
    title: str
    normalized_title: str
    page_idx: int
    heading_score: float
    reasons: tuple[str, ...]
    is_subsection: bool = False


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()


def _chapter_number(raw: str) -> int | None:
    if raw.isdigit():
        return int(raw)
    if raw in CHINESE_DIGITS:
        return CHINESE_DIGITS[raw]
    if raw.startswith("十"):
        return 10 + CHINESE_DIGITS.get(raw[1:], 0)
    if "十" in raw:
        left, right = raw.split("十", 1)
        return CHINESE_DIGITS.get(left, 1) * 10 + CHINESE_DIGITS.get(right, 0)
    return None


def _page_looks_like_toc(lines: list[str]) -> bool:
    meaningful = [line for line in lines if line.strip()]
    if not meaningful:
        return False
    dotted = sum(bool(TOC_DOTS_RE.search(line)) for line in meaningful)
    numbered = sum(bool(TRAILING_PAGE_RE.search(line)) and len(line) < 90 for line in meaningful)
    chapter_refs = sum(bool(CHAPTER_RE.search(line)) for line in meaningful)
    return dotted >= 2 or chapter_refs >= 4 or (chapter_refs >= 3 and numbered >= 3)


def is_heading_shape(text: str, keyword: str | None = None) -> bool:
    compact = re.sub(r"\s+", "", normalize_text(text)).strip(" :：")
    if not compact or len(compact) > 48 or any(mark in compact for mark in "《》“”\""):
        return False
    marker = CHAPTER_RE.match(compact) or SECTION_RE.match(compact)
    tail = compact[marker.end():] if marker else compact
    if keyword:
        pos = tail.find(keyword)
        if pos < 0 or pos > 8:
            return False
        suffix = tail[pos + len(keyword):]
    else:
        suffix = tail
    if BODY_REFERENCE_RE.search(suffix):
        return False
    suffix = re.sub(r"^[及和/]?(?:标准|要求|格式)?", "", suffix)
    suffix = re.sub(r"[（(【].*?[）)】]", "", suffix)
    suffix = re.sub(r"\d{1,3}$", "", suffix)
    return not suffix.strip(" :：、-/")


def _role_for_line(compact: str) -> tuple[str | None, str | None]:
    table_heading = compact.strip(" :：()（）《》\"“”")
    if re.fullmatch(r"(?:第[一二三四五六七八九十百0-9]+节)?(?:投标人须知前附表|投标须知前附表)(?:[（(【].*?[）)】])?", table_heading):
        return "BIDDER_NOTICE_TABLE", "投标人须知前附表"
    if CHAPTER_RE.match(compact) and any(key in compact for key in ROLE_KEYWORDS["BIDDER_NOTICE"]):
        return "BIDDER_NOTICE_CHAPTER", next(key for key in ROLE_KEYWORDS["BIDDER_NOTICE"] if key in compact)
    if SECTION_RE.match(compact) and any(compact.endswith(key) for key in ROLE_KEYWORDS["BIDDER_NOTICE"]):
        return "BIDDER_NOTICE", next(key for key in ROLE_KEYWORDS["BIDDER_NOTICE"] if compact.endswith(key))
    for role, keywords in ROLE_KEYWORDS.items():
        for keyword in keywords:
            if keyword in compact:
                return role, keyword
    return None, None


def _candidate_from_line(line: str, line_idx: int, lines: list[str], page_idx: int, toc_page: bool) -> Candidate | None:
    normalized = normalize_text(line)
    compact = re.sub(r"\s+", "", normalized)
    role, keyword = _role_for_line(compact)
    if role is None or keyword is None:
        return None

    chapter_match = CHAPTER_RE.search(compact)
    is_subsection = role in {"BIDDER_NOTICE_TABLE", "QUALIFICATION", "AWARD_SELECTION"}
    if role in {"QUALIFICATION", "AWARD_SELECTION"}:
        stripped = re.sub(r"[（(].*?[）)]", "", compact).strip(" :：()（）《》\"“”")
        section_prefix = re.compile(r"^第[一二三四五六七八九十0-9]+节")
        allowed = any(stripped == key or (section_prefix.match(stripped) and stripped.endswith(key)) for key in ROLE_KEYWORDS[role])
        if len(compact) > 32 or not allowed:
            return None
    if chapter_match and not is_subsection:
        # A real chapter title begins with the chapter marker. Body references such as
        # “符合第二章《投标人须知》第…” must never become section starts.
        if not is_heading_shape(compact, keyword):
            return None
    if not chapter_match and not is_subsection:
        # Unnumbered headings are allowed only when they look like a standalone heading.
        if not is_heading_shape(compact, keyword):
            return None

    score = 0.0
    reasons: list[str] = []
    if chapter_match:
        score += 3.0
        reasons.append("chapter_pattern")
        number = _chapter_number(chapter_match.group(1))
        expected = {"ANNOUNCEMENT": 1, "BIDDER_NOTICE_CHAPTER": 2, "EVALUATION": 3, "CONTRACT": 4}.get(role)
        if expected and number == expected:
            score += 2.0
            reasons.append("expected_chapter_number")
    if len(compact) <= 28:
        score += 1.5
        reasons.append("short_heading")
    if line_idx <= 12:
        score += 1.0
        reasons.append("near_page_top")
    if normalized.strip() == normalized and len(normalized) <= 50:
        score += 0.5
        reasons.append("standalone_line")
    page_chars = sum(len(value) for value in lines)
    if page_chars >= 250:
        score += 0.75
        reasons.append("nearby_body_text")
    if TOC_DOTS_RE.search(normalized):
        score -= 6.0
        reasons.append("toc_dot_pattern")
    elif chapter_match and TRAILING_PAGE_RE.search(normalized) and len(compact) > len(keyword) + 4:
        score -= 2.5
        reasons.append("trailing_page_number")
    if toc_page:
        score -= 5.0
        reasons.append("toc_page_density")
    if is_subsection:
        score += 1.0
        reasons.append("subsection_exact_keyword")
    return Candidate(role, normalized, keyword, page_idx, round(score, 3), tuple(reasons), is_subsection)


def find_candidates(pages: list[dict[str, Any]]) -> list[Candidate]:
    candidates: list[Candidate] = []
    for page in pages:
        lines = [normalize_text(line) for line in page["text"].splitlines() if normalize_text(line)]
        toc_page = _page_looks_like_toc(lines)
        for line_idx, line in enumerate(lines[:80]):
            candidate = _candidate_from_line(line, line_idx, lines, page["page_idx"], toc_page)
            if candidate and candidate.heading_score >= 1.5:
                candidates.append(candidate)
    return candidates


def _select_role_candidate(role_candidates: list[Candidate]) -> Candidate:
    # Score dominates. TOC candidates are already strongly penalized; on near-ties the
    # earlier occurrence is the actual chapter, while later hits are usually appendices.
    best_score = max(item.heading_score for item in role_candidates)
    near_best = [item for item in role_candidates if item.heading_score >= best_score - 0.75]
    return min(near_best, key=lambda item: item.page_idx)


def _keyword_page_index(pages: list[dict[str, Any]], sections: list[dict[str, Any]], keywords: list[str]) -> dict[str, Any]:
    per_keyword = {keyword: [] for keyword in keywords}
    page_hits: list[dict[str, Any]] = []
    for page in pages:
        text = normalize_text(page["text"])
        hits = {keyword: text.count(keyword) for keyword in keywords if keyword and keyword in text}
        if not hits:
            continue
        containing = [section for section in sections if section["start_page_idx"] <= page["page_idx"] <= section["end_page_idx"]]
        containing.sort(key=lambda section: (section["end_page_idx"] - section["start_page_idx"], section["section_id"]))
        section = containing[0] if containing else None
        location = {
            "pdf_page": page["pdf_page"],
            "page_idx": page["page_idx"],
            "section_id": section["section_id"] if section else None,
            "section_role": section["section_role"] if section else "OTHER",
            "hits": hits,
        }
        page_hits.append(location)
        for keyword, count in hits.items():
            per_keyword[keyword].append({key: value for key, value in location.items() if key != "hits"} | {"count": count})
    return {
        "purpose": "range_expansion_only_not_violation_judgment",
        "keywords": keywords,
        "by_keyword": per_keyword,
        "pages": page_hits,
    }


def locate_sections(scan: dict[str, Any], keyword_index_terms: list[str] | None = None) -> dict[str, Any]:
    candidates = find_candidates(scan["pages"])
    role_matches = {
        role: [item for item in candidates if item.role == role]
        for role in ROLE_KEYWORDS
    }
    chosen: dict[str, Candidate] = {}
    for role in ("CONTRACT", "DETERMINATION", "TECH_SPEC", "FORM_TEMPLATE"):
        if role_matches[role]:
            chosen[role] = _select_role_candidate(role_matches[role])
    evaluation_matches = role_matches["EVALUATION"]
    evaluation_boundary = min(
        [chosen[role].page_idx for role in ("DETERMINATION", "CONTRACT") if role in chosen],
        default=None,
    )
    if evaluation_boundary is not None:
        ordered = [item for item in evaluation_matches if item.page_idx < evaluation_boundary]
        evaluation_matches = ordered or evaluation_matches
    if evaluation_matches:
        chosen["EVALUATION"] = _select_role_candidate(evaluation_matches)
    for role in ("ANNOUNCEMENT", "BIDDER_NOTICE_CHAPTER", "BIDDER_NOTICE"):
        matches = role_matches[role]
        if "EVALUATION" in chosen:
            ordered = [item for item in matches if item.page_idx < chosen["EVALUATION"].page_idx]
            matches = ordered or matches
        if matches:
            chosen[role] = _select_role_candidate(matches)
    for role in ("QUALIFICATION", "AWARD_SELECTION"):
        matches = role_matches[role]
        if matches:
            chosen[role] = _select_role_candidate(matches)
    selected: list[Candidate] = list(chosen.values())
    table_matches = [item for item in candidates if item.role == "BIDDER_NOTICE_TABLE"]
    if table_matches:
        selected.append(_select_role_candidate(table_matches))

    selected.sort(key=lambda item: item.page_idx)
    sections: list[dict[str, Any]] = []
    major_selected = [item for item in selected if not item.is_subsection]
    for index, item in enumerate(selected):
        # A subsection mention must not truncate a major chapter. Both major chapters
        # and the bidder-notice table end at the next major chapter boundary.
        later_starts = [other.page_idx for other in major_selected if other.page_idx > item.page_idx]
        if item.role == "ANNOUNCEMENT":
            later_starts.extend(other.page_idx for other in selected if other.page_idx > item.page_idx and other.role == "BIDDER_NOTICE_TABLE")
        if item.role == "BIDDER_NOTICE_TABLE":
            later_starts.extend(
                candidate.page_idx
                for candidate in candidates
                if candidate.page_idx > item.page_idx
                and candidate.role in {"BIDDER_NOTICE", "BIDDER_NOTICE_CHAPTER", "EVALUATION", "DETERMINATION", "CONTRACT"}
                and candidate.heading_score >= 3.0
            )
        end_page_idx = (min(later_starts) - 1) if later_starts else scan["page_count"] - 1
        sections.append(
            {
                "section_id": f"CH{index + 1:02d}",
                "section_role": item.role,
                "title": item.title,
                "normalized_title": item.normalized_title,
                "pdf_page_start": item.page_idx + 1,
                "pdf_page_end": end_page_idx + 1,
                "start_page_idx": item.page_idx,
                "end_page_idx": end_page_idx,
                "confidence": round(max(0.0, min(1.0, item.heading_score / 8.0)), 3),
                "heading_score": item.heading_score,
                "selection_reasons": list(item.reasons),
                "detected_by": "heading_shape_and_score",
                "evidence": {"heading_text": item.title, "page_idx": item.page_idx},
            }
        )
    result = {
        "doc_id": scan["doc_id"],
        "file_name": scan["file_name"],
        "file_path": scan["file_path"],
        "file_sha256": scan["file_sha256"],
        "page_count": scan["page_count"],
        "scanner": {
            "extraction_engine": scan.get("extraction_engine", "synthetic_test"),
            "warnings": scan.get("scan_warnings", []),
        },
        "sections": sections,
        "diagnostics": {
            "candidate_count": len(candidates),
            "selected_roles": [section["section_role"] for section in sections],
            "missing_core_roles": [role for role in ("ANNOUNCEMENT", "EVALUATION") if role not in {s["section_role"] for s in sections}],
        },
    }
    result["keyword_page_index"] = _keyword_page_index(scan["pages"], sections, keyword_index_terms or [])
    return result
