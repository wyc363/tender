from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .config import PipelineConfig
from .job_models import JobRecord
from .job_store import JobStore
from .utils import sha256_file, sha256_value


@dataclass(frozen=True)
class JobSubmission:
    job: JobRecord
    created: bool


class JobService:
    def __init__(self, config: PipelineConfig, store: JobStore | None = None):
        self.config = config
        self.store = store or JobStore(
            config.effective_job_database_path,
            busy_timeout_ms=config.job_busy_timeout_ms,
        )

    def submit_local_file(self, pdf_path: str | Path, *, chat_id: str | None = None,
                          document_id: str | None = None, rule_id: str | None = None) -> JobSubmission:
        source = Path(pdf_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        if source.suffix.lower() != ".pdf":
            raise ValueError(f"Only local PDF input is supported in Phase 3: {source}")
        selected_rule = rule_id or self.config.enabled_review_points[0]
        if selected_rule not in self.config.enabled_review_points:
            raise ValueError(f"Review point is not enabled: {selected_rule}")
        digest = sha256_file(source)
        selected_document_id = document_id or f"DOC_{digest[:8]}"
        points = tuple(self.config.enabled_review_points)
        normalized_chat_id = chat_id.strip() if chat_id and chat_id.strip() else None
        idempotency_key = (
            sha256_value({
                "chat_id": normalized_chat_id,
                "file_sha256": digest,
                "enabled_review_points": sorted(points),
            })
            if normalized_chat_id else None
        )
        job_id = f"JOB_{uuid4().hex.upper()}"
        job_output_dir = (
            self.config.output_root / ".jobs" / job_id / selected_document_id
        ).resolve()
        job, created = self.store.create_job(
            job_id=job_id,
            chat_id=normalized_chat_id,
            source_path=str(source),
            file_sha256=digest,
            document_id=selected_document_id,
            rule_id=selected_rule,
            enabled_review_points=points,
            idempotency_key=idempotency_key,
            output_dir=str(job_output_dir),
            max_attempts=self.config.job_max_attempts,
        )
        return JobSubmission(job=job, created=created)
