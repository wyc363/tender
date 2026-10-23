from __future__ import annotations

import re
from collections import defaultdict
from statistics import median

from .text_utils_v2 import normalize_text, similarity


def _pos(page_idx: int, bbox: list | None) -> tuple[int, float]:
    return page_idx, float(bbox[1]) if bbox else 0.0


def _overlap(a: list | None, b: list | None) -> float:
    if not a or not b:
        return 0.0
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    area = max(0, x1 - x0) * max(0, y1 - y0)
    base = max(1.0, (a[2] - a[0]) * (a[3] - a[1]))
    return area / base


def split_text_by_verified_headings(text: str, headings: list[str]) -> list[dict]:
    """Split only on already verified heading strings; never infer a new heading here."""
    if not text or not headings:
        return [{"text": text, "heading": None}]
    normalized = normalize_text(text)
    hits = []
    for heading in headings:
        needle = normalize_text(heading)
        at = normalized.find(needle)
        if at >= 0:
            hits.append((at, needle, heading))
    if not hits:
        return [{"text": text, "heading": None}]
    # Build normalized-to-raw positions so whitespace removal does not break offsets.
    norm_to_raw = []
    for i, char in enumerate(text):
        if normalize_text(char):
            norm_to_raw.append(i)
    cuts = sorted({norm_to_raw[min(at, len(norm_to_raw) - 1)] for at, _, _ in hits})
    result = []
    if cuts[0] > 0 and text[:cuts[0]].strip():
        result.append({"text": text[:cuts[0]], "heading": None})
    hit_by_cut = {norm_to_raw[min(at, len(norm_to_raw) - 1)]: heading for at, _, heading in hits}
    for i, cut in enumerate(cuts):
        end = cuts[i + 1] if i + 1 < len(cuts) else len(text)
        if text[cut:end].strip():
            result.append({"text": text[cut:end], "heading": hit_by_cut.get(cut)})
    return result


def build_canonical_blocks(doc_id, atoms, representations, native_pages, outline_roles, anchors):
    verified = {x["outline_id"]: x for x in anchors if x["status"] == "VERIFIED"}
    anchor_by_atom = defaultdict(list)
    anchor_by_page = defaultdict(list)
    role_by_id = {x["outline_id"]: x for x in outline_roles}
    for oid, a in verified.items():
        row = role_by_id[oid]
        if row["outline_role"] not in {"STRUCTURAL_SECTION", "TOPIC_SECTION", "ATTACHMENT_SECTION"}:
            continue
        for aid in (a.get("anchor") or {}).get("source_atom_ids", []):
            anchor_by_atom[aid].append(row)
        anchor = a.get("anchor") or {}
        if anchor.get("page_idx") is not None:
            anchor_by_page[anchor["page_idx"]].append((row, anchor))

    blocks = []
    target_by_atom = defaultdict(list)
    for atom in atoms:
        role = atom["representation_role"]
        typ = atom.get("parser_type")
        if role == "PHYSICAL_PRIMARY_SOURCE_ATOM" and typ == "table":
            continue
        bbox = atom.get("bbox_pdf")
        text = atom.get("text", "")
        if role == "DISCARDED_SOURCE_ATOM":
            btype = {"page_header": "HEADER_FOOTER", "page_footer": "HEADER_FOOTER", "page_number": "PAGE_NUMBER"}.get(typ, "DECORATIVE")
            flags = ["NON_BUSINESS_CONTENT"]
        elif role == "PHYSICAL_PRIMARY_SOURCE_ATOM" and typ == "image":
            btype = "CONTENT_IMAGE_CANDIDATE"; flags = ["PHYSICAL_IMAGE_SOURCE"]
        else:
            btype = {"title": "BODY_TEXT", "paragraph": "BODY_TEXT", "list": "LIST", "equation": "EQUATION"}.get(typ, "BODY_TEXT")
            flags = ["MINERU_TITLE_CANDIDATE"] if typ == "title" else []

        headings = anchor_by_atom.get(atom["source_atom_id"], [])
        # Native-only anchors can still override a MinerU title/text block by overlap and text similarity.
        if not headings and bbox:
            for row, anchor in anchor_by_page.get(atom["page_idx"], []):
                if _overlap(bbox, anchor.get("bbox_pdf")) > .25 and similarity(text, row["raw_title"]) >= .7:
                    headings.append(row)
        parts = split_text_by_verified_headings(text, [x["raw_title"] for x in headings])
        for k, part in enumerate(parts):
            bid = f"{doc_id}-B{len(blocks)+1:06d}"
            heading = next((x for x in headings if normalize_text(x["raw_title"]) in normalize_text(part["text"])), None)
            current_type = "HEADING" if heading else btype
            current_flags = [x for x in flags if x != "MINERU_TITLE_CANDIDATE"] if heading else list(flags)
            native = []
            for line in native_pages[atom["page_idx"]]:
                if _overlap(bbox, line.get("bbox_pdf")) > .08:
                    native.append(line)
            block = {
                "canonical_block_id": bid, "block_type": current_type, "page_uid": atom["page_uid"], "page_idx": atom["page_idx"],
                "bbox_pdf": bbox, "text_canonical": part["text"], "text_mineru": text,
                "text_native": "".join(x["text"] for x in native), "source_atom_ids": [atom["source_atom_id"]],
                "representation_ids": [atom["representation_id"]] if atom.get("representation_id") else [],
                "native_span_refs": [x["native_ref"] for x in native], "source_outline_ids": [heading["outline_id"]] if heading else [],
                "review_enabled": False if current_type in {"HEADING", "HEADER_FOOTER", "PAGE_NUMBER", "DECORATIVE"} else True,
                "quality_flags": current_flags,
            }
            blocks.append(block); target_by_atom[atom["source_atom_id"]].append(bid)
    return sorted(blocks, key=lambda x: _pos(x["page_idx"], x.get("bbox_pdf"))), target_by_atom


def build_table_groups(doc_id, atoms, representations):
    fragments = [a for a in atoms if a["representation_role"] == "PHYSICAL_PRIMARY_SOURCE_ATOM" and a.get("parser_type") == "table"]
    reps = [r for r in representations if r["representation_role"] == "LOGICAL_REPRESENTATION" and r.get("parser_type") == "table"]
    by_page = defaultdict(list)
    for r in reps:
        by_page[r.get("page_idx_hint")].append(r)
    groups = []; last_fragment = None
    for frag in sorted(fragments, key=lambda x: _pos(x["page_idx"], x.get("bbox_pdf"))):
        candidates = by_page.get(frag["page_idx"], [])
        best = max(candidates, key=lambda r: similarity(frag.get("text", ""), r.get("text", "")), default=None)
        # Conservative continuation rule: adjacent pages, compatible horizontal
        # geometry, and an empty continuation representation. This avoids joining
        # two ordinary neighbouring tables merely because their pages are adjacent.
        join_previous = False
        if groups and last_fragment and frag["page_idx"] == last_fragment["page_idx"] + 1:
            a, b = last_fragment.get("bbox_norm"), frag.get("bbox_norm")
            same_columns = bool(a and b and abs(a[0] - b[0]) <= .04 and abs(a[2] - b[2]) <= .04)
            prior_empty = not normalize_text(groups[-1].get("text", ""))
            current_empty = not normalize_text(best.get("text", "") if best else frag.get("text", ""))
            join_previous = same_columns and (prior_empty or current_empty)
        gid = groups[-1]["table_group_id"] if join_previous else f"{doc_id}-TG{len(groups)+1:05d}"
        image_candidate = bool(best and best.get("image_source"))
        if join_previous:
            group = groups[-1]; group["page_end"] = frag["page_idx"]; group["grouping_status"] = "CROSS_PAGE_CONFIRMED"
            group["physical_fragment_source_atom_ids"].append(frag["source_atom_id"])
            if best: group["logical_representation_ids"].append(best["representation_id"])
            group["content_image_candidate"] = group["content_image_candidate"] or image_candidate
        else:
            group = {
                "table_group_id": gid, "grouping_status": "SINGLE_PAGE_CONFIRMED", "page_start": frag["page_idx"], "page_end": frag["page_idx"],
                "physical_fragment_source_atom_ids": [frag["source_atom_id"]], "logical_representation_ids": [best["representation_id"]] if best else [],
                "canonical_target_ids": [], "content_image_candidate": image_candidate,
                "text": best.get("text", "") if best else frag.get("text", ""), "quality_flags": [] if best else ["MISSING_V2_LOGICAL_REPRESENTATION"],
            }
            groups.append(group)
        frag["canonical_target_ids"].append(gid)
        if best:
            best["physical_source_atom_ids"].append(frag["source_atom_id"]); best["canonical_target_ids"].append(gid)
        last_fragment = frag
    return groups


def detect_local_headings(doc_id, blocks, native_pages, canonical_outline):
    covered = {normalize_text(x["title_raw"]) for x in canonical_outline}
    native_sizes = {i: median([x["font_size_avg"] for x in lines] or [10]) for i, lines in enumerate(native_pages)}
    out = []
    first_section = min((_pos(x["anchor"]["page_idx"], x["anchor"].get("bbox_pdf")) for x in canonical_outline), default=(0, 0))
    strong = re.compile(r"^(附件[一二三四五六七八九十0-9]+|附录|附表|第[一二三四五六七八九十0-9]+[章节]|[一二三四五六七八九十]+、)")
    for b in blocks:
        if _pos(b["page_idx"], b.get("bbox_pdf")) < first_section:
            continue
        if "MINERU_TITLE_CANDIDATE" not in b["quality_flags"] or not b["text_canonical"].strip():
            continue
        norm = normalize_text(b["text_canonical"])
        if norm in covered or len(norm) > 45:
            continue
        lines = [x for x in native_pages[b["page_idx"]] if _overlap(b.get("bbox_pdf"), x.get("bbox_pdf")) > .08]
        font = max([x["font_size_max"] for x in lines] or [0]); ratio = font / max(1, native_sizes[b["page_idx"]])
        score = .45 + (.25 if strong.match(norm) else 0) + (.2 if ratio >= 1.12 else 0) + (.1 if len(norm) <= 24 else 0)
        status = "PROMOTED" if score >= .75 else "CANDIDATE_ONLY"
        lid = f"{doc_id}-LH{len(out)+1:05d}"
        out.append({"local_heading_id": lid, "canonical_block_id": b["canonical_block_id"], "title": b["text_canonical"], "page_idx": b["page_idx"],
                    "page_uid": b["page_uid"], "bbox_pdf": b.get("bbox_pdf"), "confidence": round(score, 3), "status": status,
                    "boundary_strength": "SOFT", "evidence": {"mineru_title": True, "native_font_ratio": round(ratio, 3), "numbering_signal": bool(strong.match(norm))},
                    "quality_flags": [] if status == "PROMOTED" else ["LOCAL_HEADING_NOT_PROMOTED"]})
        if status == "PROMOTED":
            b["block_type"] = "LOCAL_HEADING"; b["review_enabled"] = False; b["quality_flags"] = [x for x in b["quality_flags"] if x != "MINERU_TITLE_CANDIDATE"]
    return out


def classify_front_matter(blocks, canonical_outline):
    first = min((_pos(x["anchor"]["page_idx"], x["anchor"].get("bbox_pdf")) for x in canonical_outline if x["outline_role"] == "STRUCTURAL_SECTION"), default=(0, 0))
    result = {"COVER": [], "PREFACE": [], "TOC": []}
    toc_pages = set()
    for b in blocks:
        if _pos(b["page_idx"], b.get("bbox_pdf")) >= first:
            continue
        t = normalize_text(b["text_canonical"])
        if t == "目录" or (len(t) < 80 and re.search(r"\.{2,}\d+$", b["text_canonical"])):
            toc_pages.add(b["page_idx"])
    for b in blocks:
        if _pos(b["page_idx"], b.get("bbox_pdf")) >= first:
            continue
        t = normalize_text(b["text_canonical"])
        if b["page_idx"] in toc_pages:
            kind = "TOC"
        elif any(k in t for k in ("使用说明", "编制说明", "前言")):
            kind = "PREFACE"
        else:
            kind = "COVER"
        result[kind].append(b["canonical_block_id"]); b["front_matter_role"] = kind
    return result


def build_document_tree(doc_id, canonical_outline, local_headings, blocks, table_groups, front_matter, page_count):
    sections = [{**x, "node_type": x["outline_role"], "local_heading_ids": [], "content_block_ids": [], "table_group_ids": []} for x in canonical_outline]
    by_id = {x["section_id"]: x for x in sections}
    starts = [(x["anchor"]["page_idx"], (x["anchor"].get("bbox_pdf") or [0, 0, 0, 0])[1]) for x in sections]
    for i, node in enumerate(sections):
        end = (page_count - 1, 10**9)
        for j in range(i + 1, len(sections)):
            if sections[j]["raw_level"] <= node["raw_level"]:
                end = starts[j]; break
        node["range"] = {"start": starts[i], "end_exclusive": end}
    def owner(pos):
        eligible = [x for x in sections if tuple(x["range"]["start"]) <= pos < tuple(x["range"]["end_exclusive"])]
        return max(eligible, key=lambda x: x["canonical_depth"], default=None)
    for h in local_headings:
        if h["status"] == "PROMOTED":
            p = owner(_pos(h["page_idx"], h.get("bbox_pdf")))
            if p:
                h["parent_section_id"] = p["section_id"]; p["local_heading_ids"].append(h["local_heading_id"])
    front_ids = {x for values in front_matter.values() for x in values}
    for b in blocks:
        if b["canonical_block_id"] in front_ids or b["block_type"] in {"HEADING", "LOCAL_HEADING"}:
            continue
        p = owner(_pos(b["page_idx"], b.get("bbox_pdf")))
        if p: p["content_block_ids"].append(b["canonical_block_id"])
    atom_page = {}
    for b in blocks:
        for aid in b["source_atom_ids"]: atom_page[aid] = _pos(b["page_idx"], b.get("bbox_pdf"))
    for g in table_groups:
        p = owner((g["page_start"], 0))
        if p: p["table_group_ids"].append(g["table_group_id"]); g["canonical_target_ids"].append(p["section_id"])
    return {"document_id": doc_id, "node_type": "DOCUMENT", "front_matter": [{"node_type": k, "content_block_ids": v} for k, v in front_matter.items()],
            "body": {"node_type": "BODY", "root_section_ids": [x["section_id"] for x in sections if not x["parent_id"]]}, "sections": sections,
            "supplementary_local_headings": [x for x in local_headings if x["status"] == "PROMOTED"]}
