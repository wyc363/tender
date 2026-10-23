from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest().upper()


def _count_jsonl(path: Path) -> int | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def discover_documents(output_root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for manifest_path in output_root.rglob("pipeline_manifest.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        document_id = manifest.get("document_id") or (manifest.get("run_identity") or {}).get("document_id")
        if not document_id:
            continue
        root = manifest_path.parent
        chunks = root / "03_chunk/chunks.jsonl"
        source_map = root / "02_parser/source_map.jsonl"
        page_blocks = root / "02_parser/page_blocks.jsonl"
        document_json = root / "01_docling/document.json"
        result[document_id] = {
            "document_id": document_id,
            "document_root": str(root),
            "project_name": root.name,
            "pipeline_manifest": str(manifest_path),
            "chunks_path": str(chunks),
            "source_map_path": str(source_map),
            "page_blocks_path": str(page_blocks),
            "document_json_path": str(document_json),
            "chunk_count": _count_jsonl(chunks),
            "source_map_count": _count_jsonl(source_map),
            "page_count": _count_jsonl(page_blocks),
            "assets_complete": all(x.exists() for x in (chunks, source_map, page_blocks, document_json)),
            "hashes": {str(x.relative_to(root)): sha256(x) for x in (chunks, source_map, page_blocks, document_json) if x.exists()},
        }
    return result
