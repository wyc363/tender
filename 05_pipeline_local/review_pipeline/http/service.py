from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import PipelineConfig
from ..job_models import JobRecord, JobStatus
from ..job_service import JobService, JobSubmission
from ..job_store import JobNotFoundError, JobStore
from .errors import ReviewHTTPError
from .input_resolver import (InputResolutionError, InputResolver,
                             SafeFileUrlResolver, UnsupportedFileUrlResolver)
from .request_adapter import adapt_client_request
from .response_adapter import adapt_client_response
from .schemas import ClientReviewRequest


class ReviewHTTPService:
    def __init__(self, config: PipelineConfig, *, store: JobStore | None = None,
                 job_service: JobService | None = None,
                 input_resolver: InputResolver | None = None):
        self.config = config
        self.store = store or (job_service.store if job_service else JobStore(
            config.effective_job_database_path,
            busy_timeout_ms=config.job_busy_timeout_ms,
        ))
        self.job_service = job_service or JobService(config, self.store)
        self.input_resolver = input_resolver or (
            SafeFileUrlResolver.from_config(config)
            if config.file_url_enabled else UnsupportedFileUrlResolver()
        )

    @staticmethod
    def _get_job(store: JobStore, job_id: str) -> JobRecord:
        try:
            return store.get_job(job_id)
        except JobNotFoundError as exc:
            raise ReviewHTTPError(404, "JOB_NOT_FOUND", "Job does not exist", job_id=job_id) from exc

    def health(self) -> dict[str, str]:
        if not self.store.health_check():
            raise ReviewHTTPError(503, "JOB_STORE_UNAVAILABLE", "Job store health check failed")
        return {"status": "ok", "service": "tender-review", "config": "ok", "job_store": "ok"}

    @staticmethod
    def _resolution_error(exc: InputResolutionError, chat_id: str) -> ReviewHTTPError:
        status = {
            "FILE_URL_RESOLUTION_NOT_IMPLEMENTED": 501,
            "FILE_URL_INVALID": 422,
            "FILE_URL_SCHEME_NOT_ALLOWED": 422,
            "FILE_URL_CREDENTIALS_NOT_ALLOWED": 422,
            "FILE_URL_HOST_NOT_ALLOWED": 403,
            "FILE_URL_DNS_FAILED": 502,
            "FILE_DOWNLOAD_FAILED": 502,
            "FILE_DOWNLOAD_TIMEOUT": 504,
            "FILE_DOWNLOAD_HTTP_ERROR": 502,
            "FILE_URL_REDIRECT_LIMIT": 502,
            "FILE_TOO_LARGE": 413,
            "FILE_NOT_PDF": 422,
            "FILE_STAGING_FAILED": 500,
        }.get(exc.code, 500)
        return ReviewHTTPError(status, exc.code, str(exc), chat_id=chat_id)

    def submit_job(self, request: ClientReviewRequest) -> tuple[str, JobSubmission]:
        """Resolve one client request and create/reuse its persistent job."""
        normalized = adapt_client_request(request)
        try:
            resolved = self.input_resolver.resolve(normalized)
        except InputResolutionError as exc:
            raise self._resolution_error(exc, normalized.chat_id) from exc
        submission = self.job_service.submit_local_file(
            Path(resolved.local_path),
            chat_id=normalized.chat_id,
        )
        return normalized.chat_id, submission

    def submit(self, request: ClientReviewRequest) -> dict[str, Any]:
        chat_id, submission = self.submit_job(request)
        job = submission.job
        return {
            "job_id": job.job_id,
            "chat_id": job.chat_id or chat_id,
            "status": job.status.value,
            "stage": job.stage.value,
            "created": submission.created,
        }

    def status(self, job_id: str) -> dict[str, Any]:
        job = self._get_job(self.store, job_id)
        return {
            "job_id": job.job_id,
            "chat_id": job.chat_id,
            "status": job.status.value,
            "stage": job.stage.value,
            "attempt_count": job.attempt_count,
            "max_attempts": job.max_attempts,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "heartbeat_at": job.heartbeat_at,
            "error_code": job.error_code,
            "error_message": (
                f"Job failed with code {job.error_code or 'PIPELINE_FAILED'}"
                if job.status is JobStatus.FAILED else
                "Job execution was interrupted"
                if job.status is JobStatus.INTERRUPTED else None
            ),
        }

    def result(self, job_id: str) -> dict[str, Any]:
        job = self._get_job(self.store, job_id)
        if job.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
            raise ReviewHTTPError(
                409, "RESULT_NOT_READY", "Job result is not ready",
                job_id=job.job_id, chat_id=job.chat_id,
            )
        if job.status is JobStatus.FAILED:
            raise ReviewHTTPError(
                409, "JOB_FAILED", f"Job failed with code {job.error_code or 'PIPELINE_FAILED'}",
                job_id=job.job_id, chat_id=job.chat_id,
            )
        if job.status is JobStatus.INTERRUPTED:
            raise ReviewHTTPError(
                409, "JOB_INTERRUPTED", "Job execution was interrupted",
                job_id=job.job_id, chat_id=job.chat_id,
            )
        if not job.result_path:
            raise ReviewHTTPError(
                500, "RESULT_FILE_MISSING", "Completed job result file is missing",
                job_id=job.job_id, chat_id=job.chat_id,
            )
        return adapt_client_response(job.result_path, job.chat_id)
