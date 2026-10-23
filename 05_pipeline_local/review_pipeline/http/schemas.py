from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ClientContentItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=512)
    url: str | None = Field(default=None, max_length=8192)


class ClientMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1, max_length=32)
    content: list[ClientContentItem] = Field(min_length=1, max_length=32)


class ClientReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    chat_id: str = Field(alias="chatId", min_length=1, max_length=256)
    stream: bool = False
    detail: bool = False
    variables: dict[str, Any] = Field(default_factory=dict)
    messages: list[ClientMessage] = Field(min_length=1, max_length=64)

    @field_validator("chat_id")
    @classmethod
    def validate_chat_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("chatId cannot be empty")
        return normalized


class HealthResponse(BaseModel):
    status: str
    service: str
    config: str
    job_store: str


class JobSubmissionResponse(BaseModel):
    job_id: str
    chat_id: str
    status: str
    stage: str
    created: bool


class JobStatusResponse(BaseModel):
    job_id: str
    chat_id: str | None
    status: str
    stage: str
    attempt_count: int
    max_attempts: int
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None
    heartbeat_at: str | None
    error_code: str | None = None
    error_message: str | None = None
