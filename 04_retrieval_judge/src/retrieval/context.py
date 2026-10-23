from __future__ import annotations


def _chunk_pages(chunk: dict) -> list[int]:
    values = []
    for key in ("page_numbers", "page_number", "page_no", "page_index"):
        value = chunk.get(key)
        if isinstance(value, list):
            values.extend(value)
        elif value is not None:
            values.append(value)
    pages = []
    for value in values:
        try:
            page = int(value)
        except (TypeError, ValueError):
            continue
        if page not in pages:
            pages.append(page)
    return pages


def add_context_windows(candidates: list[dict], chunks: list[dict], neighbors: int = 1, *,
                        table_neighbors: int | None = None,
                        expand_max_anchor_chars: int | None = None) -> list[dict]:
    """Attach exact adjacent chunk text for the Judge without changing anchor text."""
    ordered = sorted(chunks, key=lambda row: row["source_order"])
    positions = {chunk["chunk_id"]: index for index, chunk in enumerate(ordered)}

    def context_record(chunk):
        return {key: chunk.get(key) for key in (
            "chunk_id", "chunk_type", "source_order", "text", "page_numbers",
            "source_block_ids", "source_table_ids", "heading_path", "heading_context_texts",
            "document_region")}

    for candidate in candidates:
        position = positions[candidate["chunk_id"]]
        anchor = ordered[position]
        effective_neighbors = neighbors
        if anchor.get("chunk_type") == "TABLE" and table_neighbors is not None:
            effective_neighbors = table_neighbors
        elif expand_max_anchor_chars is not None and anchor.get("char_count", len(anchor["text"])) >= expand_max_anchor_chars:
            effective_neighbors = 0
        start = max(0, position - effective_neighbors)
        end = min(len(ordered), position + effective_neighbors + 1)
        window = [
            chunk for chunk in ordered[start:end]
            if chunk["document_id"] == anchor["document_id"]
        ]
        candidate["anchor_chunk_id"] = candidate["chunk_id"]
        candidate["anchor_chunk"] = context_record(anchor)
        candidate["context_before"] = [
            context_record(chunk) for chunk in window if chunk["source_order"] < anchor["source_order"]]
        candidate["context_after"] = [
            context_record(chunk) for chunk in window if chunk["source_order"] > anchor["source_order"]]
        candidate["context_chunk_ids"] = [chunk["chunk_id"] for chunk in window]
        candidate["document_text"] = "\n\n".join(chunk["text"] for chunk in window)
        candidate["context_source_line_start"] = window[0]["source_line_start"]
        candidate["context_source_line_end"] = window[-1]["source_line_end"]
        candidate["page_numbers"] = _chunk_pages(anchor)
        candidate["context_page_numbers"] = list(dict.fromkeys(
            page for chunk in window for page in _chunk_pages(chunk)
        ))
        candidate["context_expansion_neighbors"] = effective_neighbors
    return candidates
