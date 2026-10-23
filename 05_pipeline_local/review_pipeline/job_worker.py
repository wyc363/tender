from __future__ import annotations

import argparse
import json
import logging
import os
import re
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .config import PipelineConfig, load_config
from .job_models import JobRecord, JobStage, JobStateError
from .job_store import JobStore
from .orchestrator import PipelineOrchestrator
from .utils import redact, sha256_file


LOG = logging.getLogger("review_pipeline.job_worker")

PIPELINE_STAGE_TO_JOB_STAGE = {
    "input": JobStage.INPUT_CHECK,
    "docling": JobStage.DOCLING,
    "parser": JobStage.PARSER,
    "chunk": JobStage.CHUNK,
    "retrieval": JobStage.RETRIEVAL,
    "judge": JobStage.JUDGE,
    "aggregation": JobStage.AGGREGATION,
    "client_output": JobStage.CLIENT_OUTPUT,
}

REQUIRED_RESULT_FILES = (
    "review_point_result.json",
    "client_content_result.json",
    "client_api_response.json",
)


class JobExecutionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _Heartbeat:
    def __init__(self, store: JobStore, job_id: str, worker_id: str, interval_seconds: float):
        self.store = store
        self.job_id = job_id
        self.worker_id = worker_id
        self.interval_seconds = float(interval_seconds)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"heartbeat-{job_id}", daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                if not self.store.update_heartbeat(self.job_id, self.worker_id):
                    return
            except Exception:
                LOG.exception("[job_id=%s] heartbeat update failed", self.job_id)

    def __enter__(self) -> "_Heartbeat":
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._stop.set()
        self._thread.join(timeout=max(self.interval_seconds * 2, 1))


class JobWorker:
    def __init__(self, config: PipelineConfig, *, store: JobStore | None = None,
                 worker_id: str | None = None,
                 pipeline_factory: Callable[[PipelineConfig], Any] = PipelineOrchestrator):
        self.config = config
        self.store = store or JobStore(
            config.effective_job_database_path,
            busy_timeout_ms=config.job_busy_timeout_ms,
        )
        self.worker_id = worker_id or (
            f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}"
        )
        self.pipeline_factory = pipeline_factory

    def recover_stale(self) -> list[str]:
        recovered = self.store.recover_stale_running_jobs(self.config.job_stale_after_seconds)
        for job_id in recovered:
            LOG.warning("[job_id=%s] stale RUNNING job marked INTERRUPTED", job_id)
        return recovered

    def _progress(self, job: JobRecord, pipeline_stage: str) -> None:
        stage = PIPELINE_STAGE_TO_JOB_STAGE.get(pipeline_stage)
        if stage is None:
            raise JobExecutionError("PIPELINE_STAGE_UNKNOWN", f"Unknown pipeline stage: {pipeline_stage}")
        self.store.update_stage(job.job_id, self.worker_id, stage)
        LOG.info("[job_id=%s][document_id=%s] stage=%s",
                 job.job_id, job.document_id, stage.value)

    @staticmethod
    def _error_code(exc: Exception) -> str:
        if isinstance(exc, JobExecutionError):
            return exc.code
        text = str(exc)
        aggregation_code = re.search(r"\b(AGGREGATION_[A-Z0-9_]+)\b", text)
        if aggregation_code:
            return aggregation_code.group(1)
        for code in (
            "JOB_INPUT_MISSING",
            "JOB_INPUT_CHANGED",
            "RESULT_MISSING",
        ):
            if code in text:
                return code
        return "PIPELINE_FAILED"

    def _validate_input(self, job: JobRecord) -> Path:
        source = Path(job.source_path)
        if not source.is_file():
            raise JobExecutionError("JOB_INPUT_MISSING", f"Input file does not exist: {source}")
        if sha256_file(source) != job.file_sha256:
            raise JobExecutionError("JOB_INPUT_CHANGED", f"Input SHA256 changed: {source}")
        return source

    @staticmethod
    def _validate_results(job: JobRecord, summary: dict[str, Any]) -> Path:
        if summary.get("pipeline_status") != "COMPLETED":
            raise JobExecutionError(
                "PIPELINE_FAILED",
                f"Pipeline returned status {summary.get('pipeline_status')!r}",
            )
        output_dir = Path(job.output_dir)
        missing = [name for name in REQUIRED_RESULT_FILES if not (output_dir / name).is_file()]
        if missing:
            raise JobExecutionError("RESULT_MISSING", "Missing required result files: " + ", ".join(missing))
        return (output_dir / "client_api_response.json").resolve()

    def run_once(self) -> JobRecord | None:
        job = self.store.claim_next_job(self.worker_id)
        if job is None:
            return None
        LOG.info("[job_id=%s][document_id=%s] claimed attempt=%s/%s",
                 job.job_id, job.document_id, job.attempt_count, job.max_attempts)
        try:
            source = self._validate_input(job)
            pipeline = self.pipeline_factory(self.config)
            with _Heartbeat(
                self.store,
                job.job_id,
                self.worker_id,
                self.config.job_heartbeat_interval_seconds,
            ):
                summary = pipeline.run_document(
                    source,
                    job.document_id,
                    job.rule_id,
                    Path(job.output_dir).parent,
                    True,
                    None,
                    None,
                    on_stage_change=lambda stage: self._progress(job, stage),
                )
            result_path = self._validate_results(job, summary)
            completed = self.store.mark_completed(job.job_id, self.worker_id, str(result_path))
            LOG.info("[job_id=%s][document_id=%s] completed result=%s",
                     job.job_id, job.document_id, result_path)
            return completed
        except Exception as exc:
            code = self._error_code(exc)
            message = redact(str(exc))[:1000] or type(exc).__name__
            try:
                failed = self.store.mark_failed(job.job_id, self.worker_id, code, message)
            except JobStateError:
                LOG.exception("[job_id=%s] job ownership changed while handling failure", job.job_id)
                return self.store.get_job(job.job_id)
            LOG.error("[job_id=%s][document_id=%s] failed code=%s message=%s",
                      job.job_id, job.document_id, code, message)
            return failed

    def run_forever(self) -> None:
        self.recover_stale()
        LOG.info("worker_id=%s started", self.worker_id)
        while True:
            job = self.run_once()
            if job is None:
                time.sleep(self.config.job_poll_interval_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Persistent tender review job worker")
    parser.add_argument("--config", type=Path,
                        default=Path(__file__).resolve().parents[1] / "config" / "pipeline.local.yaml")
    parser.add_argument("--worker-id")
    parser.add_argument("--once", action="store_true", help="Claim at most one job and exit")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    worker = JobWorker(load_config(args.config), worker_id=args.worker_id)
    if args.once:
        recovered = worker.recover_stale()
        result = worker.run_once()
        print(json.dumps({
            "worker_id": worker.worker_id,
            "recovered_stale_jobs": recovered,
            "job": result.to_public_dict() if result else None,
        }, ensure_ascii=False, indent=2))
        return
    worker.run_forever()


if __name__ == "__main__":
    main()
