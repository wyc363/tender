from __future__ import annotations

from collections import defaultdict
from typing import Any


ROLE_MAP = {"COVER": "COVER_META", "PREFACE": "DOCUMENT_INSTRUCTION", "TOC": "TOC"}


def build_front_matter(blocks: list[dict[str, Any]], first_root_position: tuple) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for block in blocks:
        role = ROLE_MAP.get(block.get("front_matter_role"))
        box = block.get("bbox_pdf") or [0, 0, 0, 0]
        pos = (block.get("page_idx", 0), box[1], block.get("reading_order") or 0)
        if role and pos < first_root_position and (block.get("text_canonical") or "").strip():
            grouped[role].append(block)
    result = []
    for serial, role in enumerate(("COVER_META", "DOCUMENT_INSTRUCTION", "TOC"), 1):
        rows = sorted(grouped.get(role, []), key=lambda b: (b["page_idx"], (b.get("bbox_pdf") or [0, 0])[1]))
        seen, parts, provenance = set(), [], []
        for block in rows:
            text = " ".join((block.get("text_canonical") or "").split())
            if not text or text in seen:
                continue
            seen.add(text); parts.append(text)
            provenance.append({"canonical_block_id": block["canonical_block_id"], "page_idx": block["page_idx"], "bbox_pdf": block.get("bbox_pdf")})
        result.append({
            "front_matter_id": f"FM-{serial:03d}", "front_matter_type": role,
            "status": "PRESENT" if parts else "NOT_PRESENT_IN_SOURCE",
            "page_start": min((x["page_idx"] for x in provenance), default=None),
            "page_end": max((x["page_idx"] for x in provenance), default=None),
            "text": "\n\n".join(parts), "display": bool(parts), "retrieval_default": False,
            "effect_context": role == "DOCUMENT_INSTRUCTION", "structure_validation_context": role == "TOC",
            "provenance": provenance,
        })
    return result

