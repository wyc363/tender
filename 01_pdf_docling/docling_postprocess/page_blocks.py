"""Build the loss-auditable, page-local block layer used by the VLM POC.

This module is deterministic and does not call a model.  Text covered by a
Docling table is retained with a ``COVERED_BY_TABLE`` flag so that source
coverage can be audited while downstream callers can omit duplicate content.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from .furniture import repair
from .loader import Document, display
from .renderer import table_html


TEXT_LABELS = {"section_header", "list_item", "text", "caption"}


def _norm_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def _bbox_norm(doc: Document, prov: dict[str, Any]) -> list[float] | None:
    box = doc.box(prov)
    if box is None:
        return None
    left, top, right, bottom, width, height = box
    return [round(left / width, 6), round(top / height, 6),
            round(right / width, 6), round(bottom / height, 6)]


def _parent_ref(item: dict[str, Any]) -> str | None:
    parent = item.get("parent")
    return parent.get("$ref") if isinstance(parent, dict) else None


def _belongs_to_table(doc: Document, item: dict[str, Any]) -> bool:
    ref = _parent_ref(item)
    visited: set[str] = set()
    while ref and ref not in visited:
        if ref.startswith("#/tables/"):
            return True
        visited.add(ref)
        ref = _parent_ref(doc.refs.get(ref, {}))
    return False


def _vertical_overlap(a: list[float], b: list[float]) -> float:
    overlap = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return overlap / max(1e-9, min(a[3] - a[1], b[3] - b[1]))


def _horizontal_overlap(a: list[float], b: list[float]) -> float:
    overlap = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    return overlap / max(1e-9, min(a[2] - a[0], b[2] - b[0]))


def _table_cell_texts(table: dict[str, Any]) -> list[str]:
    return [str(cell.get("text", "")) for cell in
            table.get("data", {}).get("table_cells", []) if cell.get("text")]


def _covered_by_table(doc: Document, item: dict[str, Any], text_box: list[float],
                      page_tables: Iterable[dict[str, Any]]) -> str | None:
    text = _norm_text(display(item))
    if not text:
        return None
    parent_is_table = _belongs_to_table(doc, item)
    for table_entry in page_tables:
        table_box = table_entry["bbox_norm"]
        if _vertical_overlap(text_box, table_box) < 0.5:
            continue
        if _horizontal_overlap(text_box, table_box) <= 0:
            continue
        cells = table_entry["cell_texts_norm"]
        if parent_is_table or any(text in cell for cell in cells):
            return table_entry["source_ref"]
    return None


def _decision_pages(decision: dict[str, Any]) -> set[int]:
    pages = {int(p["page_no"]) for p in decision.get("prov", []) if p.get("page_no")}
    if decision.get("page_no"):
        pages.add(int(decision["page_no"]))
    return pages


def build_page_records(doc: Document, decisions: list[dict[str, Any]] | None = None,
                       suspicious: list[dict[str, Any]] | None = None
                       ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return one JSON-serializable record per physical PDF page and a report."""
    if decisions is None or suspicious is None:
        decisions, suspicious = repair(doc)

    excluded_pairs: set[tuple[str, int]] = set()
    excluded_refs_all_pages: set[str] = set()
    for decision in decisions:
        ref = decision["self_ref"]
        pages = _decision_pages(decision)
        if pages:
            excluded_pairs.update((ref, page) for page in pages)
        else:
            excluded_refs_all_pages.add(ref)
    suspicious_pairs = {(item["self_ref"], int(item["page_no"])) for item in suspicious
                        if item.get("page_no")}

    page_numbers = sorted(int(page) for page in doc.data.get("pages", {}))
    tables_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    table_prov_entries = 0
    for table in doc.data.get("tables", []):
        for prov in table.get("prov", []):
            page_no = int(prov["page_no"])
            bbox = _bbox_norm(doc, prov)
            if bbox is None:
                continue
            table_prov_entries += 1
            cell_texts = _table_cell_texts(table)
            tables_by_page[page_no].append({
                "source_ref": table["self_ref"],
                "kind": "table",
                "parser_label": table.get("label", "table"),
                "content_layer": table.get("content_layer", "body"),
                "text": " | ".join(_norm_text(value) for value in cell_texts if _norm_text(value)),
                "table_html": table_html(table),
                "bbox_norm": bbox,
                "parent_ref": _parent_ref(table),
                "flags": [],
                "cell_texts_norm": [_norm_text(value) for value in cell_texts if _norm_text(value)],
            })

    text_candidates: dict[int, list[dict[str, Any]]] = defaultdict(list)
    text_prov_entries = furniture_entries = unsupported_text_labels = missing_text_prov = 0
    for item in doc.data.get("texts", []):
        if not item.get("prov"):
            missing_text_prov += 1
        for prov in item.get("prov", []):
            text_prov_entries += 1
            page_no = int(prov["page_no"])
            if (item["self_ref"] in excluded_refs_all_pages or
                    (item["self_ref"], page_no) in excluded_pairs):
                furniture_entries += 1
                continue
            if item.get("label") not in TEXT_LABELS:
                unsupported_text_labels += 1
            bbox = _bbox_norm(doc, prov)
            flags: list[str] = []
            if (item["self_ref"], page_no) in suspicious_pairs:
                flags.append("furniture_candidate")
            covered_ref = None
            if bbox is not None:
                covered_ref = _covered_by_table(doc, item, bbox, tables_by_page.get(page_no, []))
            if covered_ref:
                flags.append("COVERED_BY_TABLE")
            text_candidates[page_no].append({
                "source_ref": item["self_ref"],
                "kind": "text",
                "parser_label": item.get("label", "text"),
                "content_layer": item.get("content_layer", "body"),
                "text": display(item),
                "bbox_norm": bbox,
                "parent_ref": _parent_ref(item),
                "flags": flags,
                **({"covered_by_table_ref": covered_ref} if covered_ref else {}),
            })

    pictures_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for picture in doc.data.get("pictures", []):
        for prov in picture.get("prov", []):
            page_no = int(prov["page_no"])
            pictures_by_page[page_no].append({
                "source_ref": picture["self_ref"],
                "parser_label": picture.get("label", "picture"),
                "content_layer": picture.get("content_layer", "body"),
                "bbox_norm": _bbox_norm(doc, prov),
                "parent_ref": _parent_ref(picture),
            })

    records: list[dict[str, Any]] = []
    block_ids: list[str] = []
    covered_count = 0
    for page_no in page_numbers:
        raw_blocks = text_candidates.get(page_no, []) + tables_by_page.get(page_no, [])
        raw_blocks.sort(key=lambda block: (
            block["bbox_norm"][1] if block.get("bbox_norm") else 2.0,
            block["bbox_norm"][0] if block.get("bbox_norm") else 2.0,
            0 if block["kind"] == "text" else 1,
            block["source_ref"],
        ))
        counters = Counter()
        blocks = []
        for block in raw_blocks:
            clean = {key: value for key, value in block.items() if key != "cell_texts_norm"}
            prefix = "B" if clean["kind"] == "text" else "T"
            counters[prefix] += 1
            clean["block_id"] = f"P{page_no:04d}-{prefix}{counters[prefix]:03d}"
            block_ids.append(clean["block_id"])
            covered_count += "COVERED_BY_TABLE" in clean["flags"]
            blocks.append(clean)
        records.append({"page_no": page_no, "blocks": blocks,
                        "pictures": pictures_by_page.get(page_no, [])})

    active_blocks = [block for record in records for block in record["blocks"]
                     if "COVERED_BY_TABLE" not in block["flags"]]
    accounted_text = sum(block["kind"] == "text" for record in records for block in record["blocks"])
    report = {
        "pages": len(records),
        "text_provenance_entries": text_prov_entries,
        "furniture_text_entries_excluded": furniture_entries,
        "text_entries_in_blocks": accounted_text,
        "covered_by_table_entries": covered_count,
        "table_provenance_entries": table_prov_entries,
        "picture_provenance_entries": sum(len(value) for value in pictures_by_page.values()),
        "active_blocks_for_future_vlm": len(active_blocks),
        "missing_text_provenance": missing_text_prov,
        "unsupported_text_labels_retained": unsupported_text_labels,
        "checks": {
            "all_non_furniture_text_accounted": accounted_text + furniture_entries == text_prov_entries,
            "unique_block_ids": len(block_ids) == len(set(block_ids)),
            "all_blocks_have_page_no": all(record["page_no"] and all(
                block["block_id"].startswith(f"P{record['page_no']:04d}-")
                for block in record["blocks"]) for record in records),
            "table_html_preserved": all(block.get("table_html", "").startswith("<table>")
                for record in records for block in record["blocks"] if block["kind"] == "table"),
            "no_missing_text_provenance": missing_text_prov == 0,
        },
    }
    return records, report


def write_page_blocks(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

