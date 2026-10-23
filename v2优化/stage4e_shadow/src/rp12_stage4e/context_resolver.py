from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


class ContextResolutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class GapRequest:
    gap_type: str
    target_fact_ids: tuple[str, ...]
    keywords: tuple[str, ...]


def _stable_id(candidate_id: str, gap_type: str, chunk_ids: Iterable[str]) -> str:
    material = "\n".join([candidate_id, gap_type, *chunk_ids]).encode("utf-8")
    return "CTX-" + hashlib.sha256(material).hexdigest()[:16].upper()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class ContextResolver:
    """Deterministic, same-document context lookup for Stage 4E.1."""

    VERSION = "rp12-stage4e-context-v0.1"

    def __init__(self, document_id: str, chunks: list[dict[str, Any]]):
        self.document_id = document_id
        self.chunks = sorted(chunks, key=lambda x: int(x.get("source_order") or 0))
        if any(c.get("document_id") != document_id for c in self.chunks):
            raise ContextResolutionError("CROSS_DOCUMENT_CHUNK_INPUT")
        self.by_id = {c["chunk_id"]: c for c in self.chunks}

    @classmethod
    def from_file(cls, document_id: str, chunks_path: Path) -> "ContextResolver":
        return cls(document_id, load_jsonl(chunks_path))

    def infer_gaps(self, facts: list[dict[str, Any]], risk_codes: list[str]) -> list[GapRequest]:
        fact_ids = tuple(str(f.get("fact_id")) for f in facts if f.get("fact_id"))
        codes = set(risk_codes)
        requests: list[GapRequest] = []
        if codes & {"CONDITIONAL_TEMPLATE_ACTIVATION_UNRESOLVED", "PARENT_ZERO_CHILD_POSITIVE_CONFLICT", "PARENT_CHILD_ACTIVATION_UNRESOLVED"}:
            requests.append(GapRequest("SCORING_PARENT_AND_TEMPLATE_ACTIVATION", fact_ids, ("获奖业绩", "0分", "0 分", "适用于", "得0.5分", "得 0.5 分")))
        if codes & {"BUSINESS_EFFECT_NOT_INDEPENDENTLY_VERIFIED", "RULE_COVERAGE_UNRESOLVED", "RULE_PACK_COVERAGE_INCOMPLETE"}:
            joined = "\n".join(str(f.get("atomic_evidence") or "") for f in facts)
            if "优先" in joined or "信用" in joined:
                requests.append(GapRequest("TIE_BREAK_OR_BUSINESS_EFFECT", fact_ids, ("综合评分相等", "优先顺序", "投标人优先", "资格认定", "加分依据")))
            else:
                requests.append(GapRequest("BUSINESS_EFFECT", fact_ids, ("资格认定", "加分依据", "作为资格", "得分")))
        refs = sorted({str(f.get("unresolved_reference")) for f in facts if f.get("unresolved_reference")})
        if refs:
            requests.append(GapRequest("EXPLICIT_REFERENCE", fact_ids, tuple(refs)))
        return requests

    def resolve(self, candidate_id: str, anchor_chunk_id: str, request: GapRequest) -> dict[str, Any]:
        anchor = self.by_id.get(anchor_chunk_id)
        if anchor is None:
            raise ContextResolutionError("ANCHOR_CHUNK_NOT_FOUND")
        selected: dict[str, tuple[dict[str, Any], str, str]] = {}

        def add(chunk: dict[str, Any], relationship: str, method: str) -> None:
            if chunk.get("document_id") != self.document_id:
                raise ContextResolutionError("CROSS_DOCUMENT_POLLUTION")
            text = str(chunk.get("text") or "")
            if not text:
                return
            selected.setdefault(chunk["chunk_id"], (chunk, relationship, method))

        add(anchor, "ANCHOR", "ANCHOR_ID")
        anchor_tables = set(anchor.get("source_table_ids") or [])
        anchor_heading = tuple(anchor.get("heading_path") or [])
        anchor_order = int(anchor.get("source_order") or 0)

        # Same physical/logical table is the strongest relationship and supports cross-page tables.
        if anchor_tables:
            for chunk in self.chunks:
                if anchor_tables & set(chunk.get("source_table_ids") or []):
                    add(chunk, "SAME_TABLE", "SOURCE_TABLE_ID")

        # Nearby chunks are admitted only when they remain in the same heading and are close.
        for chunk in self.chunks:
            if abs(int(chunk.get("source_order") or 0) - anchor_order) <= 2 and tuple(chunk.get("heading_path") or []) == anchor_heading:
                add(chunk, "SAME_SECTION_NEARBY", "HEADING_PATH_AND_SOURCE_ORDER")

        # Keyword/ref lookup is bounded to the same chapter/heading or same table.
        for chunk in self.chunks:
            text = str(chunk.get("text") or "")
            if not any(k and k in text for k in request.keywords):
                continue
            same_table = bool(anchor_tables & set(chunk.get("source_table_ids") or []))
            same_section = bool(anchor_heading and tuple(chunk.get("heading_path") or [])[:1] == anchor_heading[:1])
            if same_table or same_section:
                add(chunk, "KEYWORD_RELATED_IN_SECTION", "BOUNDED_KEYWORD")

        rows = sorted(selected.values(), key=lambda item: int(item[0].get("source_order") or 0))
        retrieved = []
        seen_text: set[str] = set()
        for chunk, relationship, method in rows:
            raw_text = str(chunk.get("text") or "")
            if raw_text in seen_text:
                continue
            seen_text.add(raw_text)
            retrieved.append(
                {
                    "chunk_id": chunk["chunk_id"],
                    "source_order": chunk.get("source_order"),
                    "text": raw_text,
                    "page_numbers": chunk.get("page_numbers") or [],
                    "source_ids": chunk.get("source_block_ids") or [],
                    "source_table_ids": chunk.get("source_table_ids") or [],
                    "heading_path": chunk.get("heading_path") or [],
                    "relationship_type": relationship,
                    "retrieval_method": method,
                }
            )

        combined = "\n".join(x["text"] for x in retrieved)
        keyword_hits = [k for k in request.keywords if k and k in combined]
        evidence_found = bool(keyword_hits)
        ambiguity_resolved = False
        gap_reason = None
        if request.gap_type == "SCORING_PARENT_AND_TEMPLATE_ACTIVATION":
            zero_parent = bool(re.search(r"获奖业绩\s*[（(]?\s*0\s*分", combined))
            positive_child = bool(re.search(r"得\s*0\.5\s*分", combined))
            explicitly_inactive = any(x in combined for x in ("本项不适用", "不适用本项目", "不予计分"))
            explicitly_active = any(x in combined for x in ("本项目采用", "本项目适用"))
            ambiguity_resolved = explicitly_inactive or explicitly_active
            if zero_parent and positive_child and not ambiguity_resolved:
                gap_reason = "PARENT_ZERO_CHILD_POSITIVE_CONFLICT_REPRODUCED"
            elif evidence_found and not ambiguity_resolved:
                gap_reason = "TEMPLATE_ACTIVATION_NOT_EXPLICIT"
        elif request.gap_type in {"TIE_BREAK_OR_BUSINESS_EFFECT", "BUSINESS_EFFECT"}:
            effect_phrase = any(x in combined for x in ("综合评分相等", "资格认定", "加分依据", "投标人优先"))
            ambiguity_resolved = effect_phrase and "RULE_PACK_COVERAGE_INCOMPLETE" not in combined
            if effect_phrase:
                gap_reason = "BUSINESS_EFFECT_FOUND_RULE_KNOWLEDGE_STILL_REQUIRED"
        elif request.gap_type == "EXPLICIT_REFERENCE":
            ambiguity_resolved = evidence_found
            if not evidence_found:
                gap_reason = "REFERENCED_CLAUSE_NOT_FOUND"

        if not evidence_found and gap_reason is None:
            gap_reason = "NO_RELEVANT_CONTEXT_FOUND"
        return {
            "context_id": _stable_id(candidate_id, request.gap_type, [x["chunk_id"] for x in retrieved]),
            "candidate_id": candidate_id,
            "document_id": self.document_id,
            "anchor_chunk_id": anchor_chunk_id,
            "target_fact_ids": list(request.target_fact_ids),
            "gap_type": request.gap_type,
            "retrieved_sections": [x["heading_path"] for x in retrieved],
            "retrieved_text": [x["text"] for x in retrieved],
            "source_ids": sorted({s for x in retrieved for s in x["source_ids"]}),
            "page_numbers": sorted({p for x in retrieved for p in x["page_numbers"]}),
            "retrieved_chunks": retrieved,
            "relationship_type": sorted({x["relationship_type"] for x in retrieved}),
            "retrieval_method": sorted({x["retrieval_method"] for x in retrieved}),
            "context_found": evidence_found,
            "resolved": ambiguity_resolved,
            "gap_reason": gap_reason,
            "keyword_hits": keyword_hits,
            "context_version": self.VERSION,
            "integrity_checks": {
                "same_document_only": all(x["chunk_id"] in self.by_id for x in retrieved),
                "text_verbatim_in_saved_chunks": all(self.by_id[x["chunk_id"]].get("text") == x["text"] for x in retrieved),
                "duplicate_text_count": len(retrieved) - len({x["text"] for x in retrieved}),
            },
        }
