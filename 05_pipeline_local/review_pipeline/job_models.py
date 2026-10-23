from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Mapping


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


class JobStage(StrEnum):
    SUBMITTED = "SUBMITTED"
    INPUT_CHECK = "INPUT_CHECK"
    DOCLING = "DOCLING"
    PARSER = "PARSER"
    CHUNK = "CHUNK"
    RETRIEVAL = "RETRIEVAL"
    JUDGE = "JUDGE"
    AGGREGATION = "AGGREGATION"
    CLIENT_OUTPUT = "CLIENT_OUTPUT"
    DONE = "DONE"


JOB_STAGE_SEQUENCE = (
    JobStage.SUBMITTED,
    JobStage.INPUT_CHECK,
    JobStage.DOCLING,
    JobStage.PARSER,
    JobStage.CHUNK,
    JobStage.RETRIEVAL,
    JobStage.JUDGE,
    JobStage.AGGREGATION,
    JobStage.CLIENT_OUTPUT,
    JobStage.DONE,
)
JOB_STAGE_POSITION = {stage: position for position, stage in enumerate(JOB_STAGE_SEQUENCE)}


ALLOWED_STATUS_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING}),
    JobStatus.RUNNING: frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.INTERRUPTED}),
    JobStatus.COMPLETED: frozenset(),
    JobStatus.FAILED: frozenset({JobStatus.QUEUED}),
    JobStatus.INTERRUPTED: frozenset({JobStatus.QUEUED}),
}


class JobStateError(RuntimeError):
    pass


def require_transition(current: JobStatus | str, target: JobStatus | str) -> None:
    source = JobStatus(current)
    destination = JobStatus(target)
    if destination not in ALLOWED_STATUS_TRANSITIONS[source]:
        raise JobStateError(f"Illegal job status transition: {source.value} -> {destination.value}")


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    chat_id: str | None
    source_type: str
    source_path: str
    file_sha256: str
    document_id: str
    rule_id: str
    enabled_review_points: tuple[str, ...]
    idempotency_key: str | None
    status: JobStatus
    stage: JobStage
    attempt_count: int
    max_attempts: int
    worker_id: str | None
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None
    heartbeat_at: str | None
    output_dir: str
    result_path: str | None
    error_code: str | None
    error_message: str | None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "JobRecord":
        return cls(
            job_id=row["job_id"],
            chat_id=row["chat_id"],
            source_type=row["source_type"],
            source_path=row["source_path"],
            file_sha256=row["file_sha256"],
            document_id=row["document_id"],
            rule_id=row["rule_id"],
            enabled_review_points=tuple(json.loads(row["enabled_review_points_json"])),
            idempotency_key=row["idempotency_key"],
            status=JobStatus(row["status"]),
            stage=JobStage(row["stage"]),
            attempt_count=int(row["attempt_count"]),
            max_attempts=int(row["max_attempts"]),
            worker_id=row["worker_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            heartbeat_at=row["heartbeat_at"],
            output_dir=row["output_dir"],
            result_path=row["result_path"],
            error_code=row["error_code"],
            error_message=row["error_message"],
        )

    def to_public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        value["stage"] = self.stage.value
        value["enabled_review_points"] = list(self.enabled_review_points)
        return value


@dataclass(frozen=True)
class JobEvent:
    id: int
    job_id: str
    event_type: str
    status: JobStatus
    stage: JobStage
    message: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "JobEvent":
        return cls(
            id=int(row["id"]),
            job_id=row["job_id"],
            event_type=row["event_type"],
            status=JobStatus(row["status"]),
            stage=JobStage(row["stage"]),
            message=row["message"],
            created_at=row["created_at"],
        )

    def to_public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        value["stage"] = self.stage.value
        return value
