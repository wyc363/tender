"""Strict response schema and local semantic validation for Phase A2."""

from __future__ import annotations

import json
from typing import Any


ALLOWED_TYPES = {"heading", "paragraph", "table", "ignore"}

PAGE_STRUCTURE_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["page_no", "reading_order", "classifications", "page_type", "confidence"],
    "properties": {
        "page_no": {"type": "integer", "minimum": 1},
        "reading_order": {"type": "array", "items": {"type": "string"}},
        "classifications": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["block_id", "type", "heading_level"],
                "properties": {
                    "block_id": {"type": "string"},
                    "type": {"type": "string", "enum": sorted(ALLOWED_TYPES)},
                    "heading_level": {"type": ["integer", "null"], "enum": [1, 2, None]},
                },
            },
        },
        "page_type": {"type": "string", "minLength": 1},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}


def parse_json_response(raw: str) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        value = json.loads(raw.strip())
    except (json.JSONDecodeError, TypeError) as exc:
        return None, [f"INVALID_JSON: {exc}"]
    if not isinstance(value, dict):
        return None, ["INVALID_SCHEMA: top-level value must be an object"]
    return value, []


def validate_page_structure(value: dict[str, Any], requested_page: int,
                            active: list[dict[str, Any]],
                            covered_ids: set[str] | None = None) -> list[str]:
    errors: list[str] = []
    required = {"page_no", "reading_order", "classifications", "page_type", "confidence"}
    extra = set(value) - required
    missing = required - set(value)
    if missing:
        errors.append("MISSING_FIELDS: " + ",".join(sorted(missing)))
    if extra:
        errors.append("EXTRA_FIELDS: " + ",".join(sorted(extra)))
    if type(value.get("page_no")) is not int:
        errors.append("PAGE_NO_TYPE_INVALID")
    if value.get("page_no") != requested_page:
        errors.append(f"PAGE_NO_MISMATCH: expected {requested_page}, got {value.get('page_no')}")

    active_by_id = {block["block_id"]: block for block in active}
    active_ids = set(active_by_id)
    covered_ids = covered_ids or set()
    reading = value.get("reading_order")
    if not isinstance(reading, list) or not all(isinstance(item, str) for item in reading):
        errors.append("READING_ORDER_NOT_STRING_ARRAY")
        reading = []
    reading_set = set(reading)
    if len(reading) != len(reading_set):
        errors.append("READING_ORDER_DUPLICATE")
    unknown_reading = reading_set - active_ids
    if unknown_reading:
        errors.append("READING_ORDER_UNKNOWN: " + ",".join(sorted(unknown_reading)))
    covered_returned = reading_set & covered_ids
    if covered_returned:
        errors.append("COVERED_BY_TABLE_RETURNED: " + ",".join(sorted(covered_returned)))
    missing_reading = active_ids - reading_set
    if missing_reading:
        errors.append("READING_ORDER_MISSING: " + ",".join(sorted(missing_reading)))

    classifications = value.get("classifications")
    if not isinstance(classifications, list):
        errors.append("CLASSIFICATIONS_NOT_ARRAY")
        classifications = []
    class_ids: list[str] = []
    for index, item in enumerate(classifications):
        if not isinstance(item, dict):
            errors.append(f"CLASSIFICATION_{index}_NOT_OBJECT")
            continue
        expected_keys = {"block_id", "type", "heading_level"}
        if set(item) != expected_keys:
            errors.append(f"CLASSIFICATION_{index}_FIELDS_INVALID")
        block_id = item.get("block_id")
        if isinstance(block_id, str):
            class_ids.append(block_id)
        else:
            errors.append(f"CLASSIFICATION_{index}_BLOCK_ID_INVALID")
            continue
        block_type = item.get("type")
        level = item.get("heading_level")
        if block_type not in ALLOWED_TYPES:
            errors.append(f"TYPE_INVALID: {block_id}")
        if block_type == "heading":
            if level not in (1, 2) or type(level) is not int:
                errors.append(f"HEADING_LEVEL_INVALID: {block_id}")
        elif level is not None:
            errors.append(f"NON_HEADING_LEVEL_NOT_NULL: {block_id}")
        if block_id in active_by_id and active_by_id[block_id]["kind"] == "table" and block_type != "table":
            errors.append(f"TABLE_TYPE_CHANGED: {block_id}")

    class_set = set(class_ids)
    if len(class_ids) != len(class_set):
        errors.append("CLASSIFICATIONS_DUPLICATE")
    unknown_class = class_set - active_ids
    if unknown_class:
        errors.append("CLASSIFICATIONS_UNKNOWN: " + ",".join(sorted(unknown_class)))
    covered_class = class_set & covered_ids
    if covered_class:
        errors.append("COVERED_BY_TABLE_CLASSIFIED: " + ",".join(sorted(covered_class)))
    missing_class = active_ids - class_set
    if missing_class:
        errors.append("CLASSIFICATIONS_MISSING: " + ",".join(sorted(missing_class)))
    if class_set != reading_set:
        errors.append("READING_CLASSIFICATION_ID_SET_MISMATCH")

    page_type = value.get("page_type")
    if not isinstance(page_type, str) or not page_type.strip():
        errors.append("PAGE_TYPE_INVALID")
    confidence = value.get("confidence")
    if (type(confidence) not in (int, float) or isinstance(confidence, bool)
            or not 0 <= confidence <= 1):
        errors.append("CONFIDENCE_INVALID")
    return errors
