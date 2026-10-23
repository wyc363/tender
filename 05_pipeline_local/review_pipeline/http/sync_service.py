from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ..config import PipelineConfig
from ..job_models import JobRecord, JobStatus
from ..job_store import JobNotFoundError, JobStore
from .errors import ReviewHTTPError
from .schemas import ClientReviewRequest
from .service import ReviewHTTPService


AsyncSleep = Callable[[float], Awaitable[None]]


class SyncJobWaiter:
    """Observe a persistent job until it reaches a terminal state.

    The waiter deliberately owns no worker operations: it never claims,
    heartbeats, requeues or mutates a job.  Every poll is one short JobStore
    read executed outside the FastAPI event loop.
    """

    def __init__(self, store: JobStore, *, timeout_seconds: float = 1800,
                 poll_interval_seconds: float = 2,
                 sleep: AsyncSleep = asyncio.sleep,
                 monotonic: Callable[[], float] = time.monotonic):
        if timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("sync wait timeout and poll interval must be positive")
        self.store = store
        self.timeout_seconds = float(timeout_seconds)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self._sleep = sleep
        self._monotonic = monotonic

    async def wait(self, job_id: str, *, chat_id: str | None = None) -> JobRecord:
        started = self._monotonic()
        while True:
            try:
                job = await asyncio.to_thread(self.store.get_job, job_id)
            except JobNotFoundError as exc:
                raise ReviewHTTPError(
                    404, "JOB_NOT_FOUND", "Job does not exist", chat_id=chat_id
                ) from exc
            if job.status is JobStatus.COMPLETED:
                return job
            if job.status is JobStatus.FAILED:
                raise ReviewHTTPError(
                    500,
                    "JOB_FAILED",
                    f"Review job failed with code {job.error_code or 'PIPELINE_FAILED'}",
                    chat_id=chat_id,
                )
            if job.status is JobStatus.INTERRUPTED:
                raise ReviewHTTPError(
                    500,
                    "JOB_INTERRUPTED",
                    "Review job execution was interrupted",
                    chat_id=chat_id,
                )
            elapsed = self._monotonic() - started
            if elapsed >= self.timeout_seconds:
                raise ReviewHTTPError(
                    504,
                    "SYNC_WAIT_TIMEOUT",
                    "Review job did not finish within the configured synchronous wait timeout.",
                    chat_id=chat_id,
                )
            await self._sleep(min(self.poll_interval_seconds, self.timeout_seconds - elapsed))


class SyncReviewService:
    """Expose a synchronous client contract over the persistent Job system."""

    def __init__(self, config: PipelineConfig, http_service: ReviewHTTPService,
                 waiter: SyncJobWaiter | None = None):
        self.config = config
        self.http_service = http_service
        self.waiter = waiter or SyncJobWaiter(
            http_service.store,
            timeout_seconds=config.client_sync_wait_timeout_seconds,
            poll_interval_seconds=config.client_sync_poll_interval_seconds,
        )

    async def review(self, request: ClientReviewRequest) -> dict[str, Any]:
        # Resolver and JobService are intentionally kept synchronous and moved
        # to a worker thread in the narrowest possible scope.
        chat_id, submission = await asyncio.to_thread(self.http_service.submit_job, request)
        await self.waiter.wait(submission.job.job_id, chat_id=chat_id)
        # Re-read the persisted terminal record and load the final response via
        # the same adapter used by GET /jobs/{job_id}/result.
        return await asyncio.to_thread(self.http_service.result, submission.job.job_id)
