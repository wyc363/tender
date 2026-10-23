from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .errors import ReviewHTTPError
from .schemas import ClientReviewRequest


@dataclass(frozen=True)
class NormalizedReviewRequest:
    chat_id: str
    file_url: str
    file_name: str | None
    stream: bool
    detail: bool
    variables: dict[str, Any]


def adapt_client_request(request: ClientReviewRequest) -> NormalizedReviewRequest:
    if request.stream:
        raise ReviewHTTPError(400, "STREAM_NOT_SUPPORTED", "stream=true is not supported")

    file_items = [
        item
        for message in request.messages
        for item in message.content
        if item.type == "file_url"
    ]
    if not file_items:
        raise ReviewHTTPError(422, "FILE_URL_MISSING", "Exactly one file_url item is required")
    if len(file_items) > 1:
        raise ReviewHTTPError(
            422,
            "MULTIPLE_FILE_URLS_NOT_SUPPORTED",
            "Only one file_url item is supported",
        )

    item = file_items[0]
    value = (item.url or "").strip()
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError as exc:
        raise ReviewHTTPError(422, "FILE_URL_INVALID", "file_url is invalid") from exc
    if not value or not parsed.scheme or not parsed.netloc or not parsed.hostname:
        raise ReviewHTTPError(422, "FILE_URL_INVALID", "file_url must be a valid HTTP or HTTPS URL")
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ReviewHTTPError(
            422, "FILE_URL_SCHEME_NOT_ALLOWED", "Only HTTP and HTTPS file_url values are allowed"
        )
    if parsed.username is not None or parsed.password is not None:
        raise ReviewHTTPError(
            422, "FILE_URL_CREDENTIALS_NOT_ALLOWED",
            "Credentials embedded in file_url are not allowed",
        )
    return NormalizedReviewRequest(
        chat_id=request.chat_id,
        file_url=value,
        file_name=(item.name or "").strip() or None,
        stream=request.stream,
        detail=request.detail,
        variables=dict(request.variables),
    )
