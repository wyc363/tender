"""Render cached page images for visual structure inspection; no OCR or API."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def inspect_pdf(pdf_path: Path) -> dict[str, Any]:
    import pymupdf

    with pymupdf.open(pdf_path) as document:
        pages = [{"page_no": index + 1, "width": page.rect.width,
                  "height": page.rect.height} for index, page in enumerate(document)]
    return {"page_count": len(pages), "pages": pages}


def render_page_images(pdf_path: Path, image_dir: Path, width_px: int = 1400,
                       quality: int = 85, force: bool = False) -> dict[str, Any]:
    if width_px < 300:
        raise ValueError("image width must be at least 300 pixels")
    if not 1 <= quality <= 100:
        raise ValueError("JPEG quality must be between 1 and 100")
    import pymupdf

    image_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = image_dir / "manifest.json"
    pdf_sha = sha256_file(pdf_path)
    expected_cache = {"pdf_sha256": pdf_sha, "width_px": width_px,
                      "jpeg_quality": quality}
    cached_manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            cached_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached_manifest = {}
    cache_compatible = all(cached_manifest.get(key) == value
                           for key, value in expected_cache.items())

    rendered = reused = total_pixels = 0
    images: list[dict[str, Any]] = []
    with pymupdf.open(pdf_path) as document:
        for index, page in enumerate(document):
            page_no = index + 1
            output = image_dir / f"page_{page_no:04d}.jpg"
            scale = width_px / page.rect.width
            valid_cache = cache_compatible and output.exists() and output.stat().st_size > 0
            cached_size = None
            if valid_cache and not force:
                try:
                    cached_pixmap = pymupdf.Pixmap(output)
                    cached_size = (cached_pixmap.width, cached_pixmap.height)
                except (RuntimeError, ValueError):
                    valid_cache = False
            if force or not valid_cache:
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                pixmap.save(output, jpg_quality=quality)
                actual_width, actual_height = pixmap.width, pixmap.height
                rendered += 1
            else:
                actual_width, actual_height = cached_size
                reused += 1
            total_pixels += actual_width * actual_height
            images.append({"page_no": page_no, "path": str(output),
                           "width_px": actual_width, "height_px": actual_height,
                           "bytes": output.stat().st_size})

    manifest = {**expected_cache, "pdf_path": str(pdf_path), "page_count": len(images),
                "rendered_this_run": rendered, "reused_from_cache": reused,
                "total_pixels": total_pixels, "images": images}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
