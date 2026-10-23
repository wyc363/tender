"""Strict A2.1 boundary schema and semantic validator."""

from __future__ import annotations

from typing import Any

from .vlm_boundary_prompt import SCHEMA_VERSION
from .vlm_schema import parse_json_response


PAGE_TYPES = {"content", "toc", "form", "mixed"}
# Conservative order matters for constrained decoding: ordinary content is the default.
BOUNDARY_ROLES = ("none", "soft", "hard")

BOUNDARY_JSON_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["schema_version", "page_no", "page_type", "elements"],
    "properties": {
        "schema_version": {"type": "string", "const": SCHEMA_VERSION},
        "page_no": {"type": "integer", "minimum": 1},
        "page_type": {"type": "string", "enum": sorted(PAGE_TYPES)},
        "elements": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["block_id", "boundary_role"],
            "properties": {
                "block_id": {"type": "string"},
                "boundary_role": {"type": "string", "enum": list(BOUNDARY_ROLES)},
            },
        }},
    },
}


def boundary_schema_for(active: list[dict[str, Any]]) -> dict[str, Any]:
    """Bind known IDs and table roles in the API schema without fixing their order."""
    variants = []
    for block in active:
        roles = ["none"] if block.get("kind") == "table" else list(BOUNDARY_ROLES)
        variants.append({"type": "object", "additionalProperties": False,
            "required": ["block_id", "boundary_role"],
            "properties": {"block_id": {"type": "string", "const": block["block_id"]},
                           "boundary_role": {"type": "string", "enum": roles}}})
    schema = {**BOUNDARY_JSON_SCHEMA, "properties": dict(BOUNDARY_JSON_SCHEMA["properties"])}
    schema["properties"]["elements"] = {"type": "array", "minItems": len(active),
        "maxItems": len(active), "items": {"oneOf": variants}}
    return schema


def validate_boundary(value: dict[str, Any], requested_page: int,
                      active: list[dict[str, Any]], covered_ids: set[str] | None = None,
                      finish_reason: str | None = None) -> list[str]:
    errors: list[str] = []
    if finish_reason == "length":
        errors.append("INVALID_TRUNCATED: finish_reason=length")
    required = {"schema_version", "page_no", "page_type", "elements"}
    if set(value) - required: errors.append("EXTRA_FIELDS: " + ",".join(sorted(set(value) - required)))
    if required - set(value): errors.append("MISSING_FIELDS: " + ",".join(sorted(required - set(value))))
    if value.get("schema_version") != SCHEMA_VERSION: errors.append("SCHEMA_VERSION_INVALID")
    if type(value.get("page_no")) is not int or value.get("page_no") != requested_page:
        errors.append(f"PAGE_NO_MISMATCH: expected {requested_page}, got {value.get('page_no')}")
    if value.get("page_type") not in PAGE_TYPES: errors.append("PAGE_TYPE_INVALID")
    elements = value.get("elements")
    if not isinstance(elements, list):
        errors.append("ELEMENTS_NOT_ARRAY"); elements = []
    active_by_id = {b["block_id"]: b for b in active}; active_ids = set(active_by_id)
    covered_ids = covered_ids or set(); ids: list[str] = []
    for index, element in enumerate(elements):
        if not isinstance(element, dict):
            errors.append(f"ELEMENT_{index}_NOT_OBJECT"); continue
        if set(element) != {"block_id", "boundary_role"}:
            errors.append(f"ELEMENT_{index}_FIELDS_INVALID")
        block_id, role = element.get("block_id"), element.get("boundary_role")
        if not isinstance(block_id, str):
            errors.append(f"ELEMENT_{index}_BLOCK_ID_INVALID"); continue
        ids.append(block_id)
        if role not in BOUNDARY_ROLES: errors.append(f"BOUNDARY_ROLE_INVALID: {block_id}")
        if role == "ignore": errors.append(f"IGNORE_NOT_ALLOWED: {block_id}")
        if block_id in active_by_id and active_by_id[block_id].get("kind") == "table" and role != "none":
            errors.append(f"TABLE_BOUNDARY_NOT_NONE: {block_id}")
    id_set = set(ids)
    if len(ids) != len(id_set): errors.append("ELEMENTS_DUPLICATE")
    unknown = id_set - active_ids
    if unknown: errors.append("ELEMENTS_UNKNOWN: " + ",".join(sorted(unknown)))
    covered = id_set & covered_ids
    if covered: errors.append("COVERED_BY_TABLE_RETURNED: " + ",".join(sorted(covered)))
    missing = active_ids - id_set
    if missing: errors.append("ELEMENTS_MISSING: " + ",".join(sorted(missing)))
    return errors
