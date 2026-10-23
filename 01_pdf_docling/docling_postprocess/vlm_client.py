"""LiteLLM client for one-page Parser VLM structure decisions."""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .vlm_config import ParserVLMSettings
from .vlm_prompt import SYSTEM_PROMPT
from .vlm_schema import PAGE_STRUCTURE_JSON_SCHEMA


@dataclass
class VLMClientResult:
    text: str
    raw_response: Any
    usage: dict[str, int | None]
    latency_seconds: float
    structured_output_mode: str
    max_pixels_forwarded: bool
    compatibility_fallbacks: list[str]
    finish_reason: str | None = None


def _openai_model_name(model: str) -> str:
    cleaned = model.strip()
    return cleaned if cleaned.startswith("openai/") else f"openai/{cleaned}"


def _response_text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if choices:
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if content is not None:
            return str(content)
    if isinstance(response, dict):
        return str(response["choices"][0]["message"]["content"])
    raise ValueError("API response has no choices[0].message.content")


def extract_finish_reason(response: Any) -> str | None:
    choices = getattr(response, "choices", None)
    if choices:
        value = getattr(choices[0], "finish_reason", None)
        return str(value) if value is not None else None
    if isinstance(response, dict):
        value = response.get("choices", [{}])[0].get("finish_reason")
        return str(value) if value is not None else None
    return None


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    for method_name in ("model_dump", "dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            result = method()
            if isinstance(result, dict):
                return result
    return {}


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract_usage(response: Any) -> dict[str, int | None]:
    usage = _as_dict(getattr(response, "usage", None))
    if not usage and isinstance(response, dict):
        usage = _as_dict(response.get("usage"))
    prompt = _int_or_none(usage.get("prompt_tokens", usage.get("input_tokens")))
    completion = _int_or_none(usage.get("completion_tokens", usage.get("output_tokens")))
    total = _int_or_none(usage.get("total_tokens"))
    return {"prompt_tokens": prompt, "input_tokens": prompt,
            "completion_tokens": completion, "output_tokens": completion,
            "total_tokens": total}


def serialize_response(response: Any) -> Any:
    value = _as_dict(response)
    if value:
        return value
    try:
        json.dumps(response)
        return response
    except (TypeError, ValueError):
        return {"repr": repr(response)}


def _looks_like_schema_compatibility_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(token in text for token in
               ("response_format", "json_schema", "structured output", "unsupported schema"))


def _looks_like_max_pixels_error(exc: Exception) -> bool:
    return bool(re.search(r"max[_ ]?pixels|image_url.*extra|unknown.*image", str(exc), re.I))


def call_page_structure(settings: ParserVLMSettings, image_path: Path, user_prompt: str,
                        litellm_module: Any = None, *, system_prompt: str | None = None,
                        response_schema: dict[str, Any] | None = None,
                        schema_name: str = "page_structure") -> VLMClientResult:
    """Call one page. Compatibility fallback is explicit in the returned audit fields."""
    if litellm_module is None:
        import litellm as litellm_module

    image_bytes = image_path.read_bytes()
    data_url = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii")
    mode = "json_schema_strict"
    forward_max_pixels = True
    fallbacks: list[str] = []
    started = time.perf_counter()
    while True:
        image_value: dict[str, Any] = {"url": data_url}
        if forward_max_pixels:
            image_value["max_pixels"] = settings.max_pixels
        messages = [
            {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": image_value},
            ]},
        ]
        response_format = ({
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True,
                            "schema": response_schema or PAGE_STRUCTURE_JSON_SCHEMA},
        } if mode == "json_schema_strict" else {"type": "json_object"})
        kwargs = {
            "model": _openai_model_name(settings.model),
            "messages": messages,
            "api_base": settings.api_base,
            "api_key": settings.api_key,
            "temperature": settings.temperature,
            "timeout": settings.timeout_seconds,
            "max_tokens": settings.max_tokens,
            "num_retries": settings.max_retries,
            "response_format": response_format,
            "extra_body": {"enable_thinking": settings.enable_thinking},
        }
        try:
            response = litellm_module.completion(**kwargs)
            return VLMClientResult(
                text=_response_text(response), raw_response=serialize_response(response),
                usage=extract_usage(response), latency_seconds=time.perf_counter() - started,
                structured_output_mode=mode, max_pixels_forwarded=forward_max_pixels,
                compatibility_fallbacks=fallbacks,
                finish_reason=extract_finish_reason(response),
            )
        except Exception as exc:
            if forward_max_pixels and _looks_like_max_pixels_error(exc):
                forward_max_pixels = False
                fallbacks.append("max_pixels_not_forwarded_after_compatibility_error")
                continue
            if mode == "json_schema_strict" and _looks_like_schema_compatibility_error(exc):
                mode = "json_object_local_strict_validation"
                fallbacks.append("json_schema_to_json_object_after_compatibility_error")
                continue
            raise
