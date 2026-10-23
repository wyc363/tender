from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pymupdf
from pypdf import PdfReader


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan_pdf(pdf_path: str | Path, doc_id: str) -> dict[str, Any]:
    """Read only the embedded PDF text layer; never runs OCR/MinerU/LLM."""
    path = Path(pdf_path).resolve()
    pages: list[dict[str, Any]] = []
    scan_warnings: list[dict[str, Any]] = []
    try:
        with pymupdf.open(path) as document:
            for page_idx in range(document.page_count):
                page = document.load_page(page_idx)
                pages.append({"pdf_page": page_idx + 1, "page_idx": page_idx, "text": page.get_text("text", sort=True) or ""})
            page_count = document.page_count
        extraction_engine = "PyMuPDF"
    except Exception as error:
        # Some source PDFs have a malformed page tree. Keep the original file immutable
        # and preserve its physical page sequence through a tolerant text-layer fallback.
        pages.clear()
        reader = PdfReader(path, strict=False)
        for page_idx, page in enumerate(reader.pages):
            try:
                text = page.extract_text() or ""
            except Exception as page_error:
                text = ""
                scan_warnings.append({"page_idx": page_idx, "pdf_page": page_idx + 1, "error": repr(page_error)})
            pages.append({"pdf_page": page_idx + 1, "page_idx": page_idx, "text": text})
        page_count = len(reader.pages)
        extraction_engine = "pypdf_fallback"
        scan_warnings.insert(0, {"scope": "document", "pymupdf_error": repr(error)})
    return {
        "doc_id": doc_id,
        "file_name": path.name,
        "file_path": str(path),
        "file_sha256": sha256_file(path),
        "page_count": page_count,
        "extraction_engine": extraction_engine,
        "scan_warnings": scan_warnings,
        "pages": pages,
    }
