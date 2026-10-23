from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import read_json


class RuleCatalogError(RuntimeError):
    pass


def default_catalog_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "rule_catalog.compiled.json"


def load_rule_catalog(path: Path | None = None) -> dict[str, Any]:
    catalog_path = (path or default_catalog_path()).resolve()
    if not catalog_path.is_file():
        raise RuleCatalogError(
            f"Compiled rule catalog is missing: {catalog_path}. "
            "Run tools/compile_rule_catalog.py first."
        )
    catalog = read_json(catalog_path)
    if catalog.get("status") != "AUTHORITATIVE_RULE_CATALOG":
        raise RuleCatalogError(f"Invalid rule catalog status: {catalog_path}")
    if not isinstance(catalog.get("review_points"), list):
        raise RuleCatalogError(f"Invalid rule catalog review_points: {catalog_path}")
    return catalog


def find_review_point(
    catalog: dict[str, Any],
    *,
    review_point_id: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    points = catalog.get("review_points") or []
    if review_point_id:
        matches = [row for row in points if row.get("review_point_id") == review_point_id]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuleCatalogError(f"Duplicate review_point_id: {review_point_id}")
    if name:
        matches = [row for row in points if row.get("name") == name]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuleCatalogError(f"Duplicate review point name: {name}")
    raise RuleCatalogError(
        f"Review point not found (review_point_id={review_point_id!r}, name={name!r})"
    )
