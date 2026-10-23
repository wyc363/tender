from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import ReviewHTTPError


def adapt_client_response(result_path: str | Path, chat_id: str | None) -> dict[str, Any]:
    source = Path(result_path)
    if not source.is_file():
        raise ReviewHTTPError(500, "RESULT_FILE_MISSING", "Completed job result file is missing")
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("response root is not an object")
        if not isinstance(payload.get("model"), str):
            raise ValueError("model is missing")
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            raise ValueError("usage is missing")
        required_usage = ("prompt_tokens", "completion_tokens", "total_tokens")
        if any(type(usage.get(key)) is not int for key in required_usage):
            raise ValueError("usage token counts are invalid")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError("choices[0] is missing")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ValueError("choices[0].message.content must be a JSON string")
        if message.get("role") != "assistant":
            raise ValueError("choices[0].message.role must be assistant")
        if choice.get("finish_reason") != "stop" or choice.get("index") != 0:
            raise ValueError("choices[0] completion metadata is invalid")
        content = json.loads(message["content"])
        if not isinstance(content, dict) or not isinstance(content.get("result"), list):
            raise ValueError("message.content result is invalid")
        if len(content["result"]) != 1 or not isinstance(content["result"][0], dict):
            raise ValueError("single review point result is required")
        if content["result"][0].get("index") != 12:
            raise ValueError("single review point index must be 12")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
        raise ReviewHTTPError(500, "RESULT_FILE_INVALID", "Completed job result file is invalid") from exc

    # Client success responses are deliberately constructed from a whitelist.
    # Internal fields such as log_file, paths, job metadata and debug details
    # remain in the pipeline artifacts but can never cross the HTTP boundary.
    return {
        "id": chat_id or str(payload.get("id") or ""),
        "model": payload["model"],
        "usage": {
            "prompt_tokens": usage["prompt_tokens"],
            "completion_tokens": usage["completion_tokens"],
            "total_tokens": usage["total_tokens"],
        },
        "choices": [{
            "message": {
                "role": "assistant",
                "content": message["content"],
            },
            "finish_reason": "stop",
            "index": 0,
        }],
    }
