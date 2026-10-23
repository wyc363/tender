from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ReviewHTTPError(RuntimeError):
    status_code: int
    code: str
    message: str
    job_id: str | None = None
    chat_id: str | None = None

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, self.message)

    def envelope(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.job_id:
            error["job_id"] = self.job_id
        if self.chat_id:
            error["chat_id"] = self.chat_id
        return {"error": error}
