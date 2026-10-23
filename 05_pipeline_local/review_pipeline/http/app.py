from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..config import PipelineConfig, load_config
from ..job_service import JobService
from ..job_store import JobStore
from .errors import ReviewHTTPError
from .input_resolver import InputResolver
from .schemas import (ClientReviewRequest, HealthResponse,
                      JobStatusResponse, JobSubmissionResponse)
from .service import ReviewHTTPService
from .sync_service import SyncJobWaiter, SyncReviewService


LOG = logging.getLogger("review_pipeline.http")
DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "pipeline.local.yaml"


def create_app(config: PipelineConfig | None = None, *, job_store: JobStore | None = None,
               job_service: JobService | None = None,
               input_resolver: InputResolver | None = None,
               sync_waiter: SyncJobWaiter | None = None) -> FastAPI:
    runtime_config = config or load_config(DEFAULT_CONFIG)
    service = ReviewHTTPService(
        runtime_config,
        store=job_store,
        job_service=job_service,
        input_resolver=input_resolver,
    )
    sync_service = SyncReviewService(runtime_config, service, waiter=sync_waiter)
    app = FastAPI(
        title="Tender Review Job API",
        version="0.6.0",
        docs_url="/docs" if runtime_config.http_docs_enabled else None,
        redoc_url="/redoc" if runtime_config.http_docs_enabled else None,
    )
    app.state.review_service = service
    app.state.sync_review_service = sync_service
    app.state.worker_started = False

    @app.exception_handler(ReviewHTTPError)
    async def handle_review_error(request: Request, exc: ReviewHTTPError) -> JSONResponse:
        LOG.warning("HTTP error code=%s job_id=%s chat_id=%s",
                    exc.code, exc.job_id, exc.chat_id)
        return JSONResponse(status_code=exc.status_code, content=exc.envelope())

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"error": {
                "code": "REQUEST_VALIDATION_FAILED",
                "message": "Request body does not match the required schema",
            }},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        LOG.exception("Unexpected HTTP service error")
        return JSONResponse(
            status_code=500,
            content={"error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "Internal server error",
            }},
        )

    @app.get("/health", response_model=HealthResponse)
    def health() -> dict[str, str]:
        return service.health()

    prefix = runtime_config.http_api_prefix

    @app.post(f"{prefix}/review/jobs", response_model=JobSubmissionResponse, status_code=202)
    def submit_review(request: ClientReviewRequest) -> dict:
        return service.submit(request)

    if runtime_config.client_sync_enabled:
        @app.post(runtime_config.client_sync_route_path)
        async def review_sync(request: ClientReviewRequest) -> dict:
            return await sync_service.review(request)

    @app.get(f"{prefix}/review/jobs/{{job_id}}", response_model=JobStatusResponse)
    def get_job_status(job_id: str) -> dict:
        return service.status(job_id)

    @app.get(f"{prefix}/review/jobs/{{job_id}}/result")
    def get_job_result(job_id: str) -> dict:
        return service.result(job_id)

    return app
