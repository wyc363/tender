from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from review_pipeline.config import PipelineConfig, load_config
from review_pipeline.job_cli import main as job_cli_main
from review_pipeline.job_models import JobStage, JobStateError, JobStatus
from review_pipeline.job_service import JobService
from review_pipeline.job_store import JobStore
from review_pipeline.job_worker import JobWorker, REQUIRED_RESULT_FILES
from review_pipeline.orchestrator import PipelineOrchestrator


RULE_ID = "RP_REGION_INDUSTRY_AWARD"


def make_config(tmp_path: Path, *, heartbeat: float = 0.02, stale: float = 0.05,
                max_attempts: int = 2) -> PipelineConfig:
    projects: dict[str, Path] = {}
    for name in ("docling", "parser", "chunk", "retrieval", "judge"):
        projects[name] = tmp_path / name
        projects[name].mkdir(parents=True, exist_ok=True)
    rule = tmp_path / "rule.json"
    retrieval = tmp_path / "retrieval.json"
    prompt = tmp_path / "prompt.json"
    rule.write_text("{}", encoding="utf-8")
    retrieval.write_text(json.dumps({"review_item_id": RULE_ID}), encoding="utf-8")
    prompt.write_text("{}", encoding="utf-8")
    parser_env = tmp_path / "parser.env"
    judge_env = tmp_path / "judge.env"
    parser_env.write_text("", encoding="utf-8")
    judge_env.write_text("", encoding="utf-8")
    (tmp_path / "pipeline.yaml").write_text("test: true\n", encoding="utf-8")
    py = Path(sys.executable)
    return PipelineConfig(
        config_path=tmp_path / "pipeline.yaml",
        project_root=tmp_path,
        output_root=tmp_path / "out",
        rule_store_path=rule,
        retrieval_config_path=retrieval,
        prompt_program_path=prompt,
        docling_project=projects["docling"],
        parser_project=projects["parser"],
        chunk_project=projects["chunk"],
        retrieval_project=projects["retrieval"],
        judge_project=projects["judge"],
        docling_python=py,
        parser_python=py,
        chunk_python=py,
        retrieval_python=py,
        judge_python=py,
        parser_env_file=parser_env,
        judge_env_file=judge_env,
        enabled_review_points=(RULE_ID,),
        job_database_path=tmp_path / "state" / "jobs.sqlite3",
        job_settings={
            "poll_interval_seconds": 0.01,
            "heartbeat_interval_seconds": heartbeat,
            "stale_after_seconds": stale,
            "max_attempts": max_attempts,
            "worker_concurrency": 1,
            "busy_timeout_ms": 5000,
        },
    )


def make_pdf(tmp_path: Path, name: str = "input.pdf", content: bytes = b"%PDF-test") -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def submit(config: PipelineConfig, pdf: Path, *, chat_id: str | None = None):
    return JobService(config).submit_local_file(pdf, chat_id=chat_id)


class SuccessfulFakePipeline:
    def __init__(self, config: PipelineConfig, *, sleep_seconds: float = 0,
                 omit: str | None = None, failure: Exception | None = None):
        self.config = config
        self.sleep_seconds = sleep_seconds
        self.omit = omit
        self.failure = failure

    def run_document(self, pdf, document_id, rule_id, output_root, resume,
                     stop_after, regression_profile, *, on_stage_change=None):
        for stage in ("input", "docling", "parser", "chunk", "retrieval", "judge", "aggregation", "client_output"):
            if on_stage_change:
                on_stage_change(stage)
        if self.sleep_seconds:
            time.sleep(self.sleep_seconds)
        if self.failure:
            raise self.failure
        root = Path(output_root) / document_id
        root.mkdir(parents=True, exist_ok=True)
        for name in REQUIRED_RESULT_FILES:
            if name != self.omit:
                (root / name).write_text("{}", encoding="utf-8")
        return {"pipeline_status": "COMPLETED", "document_id": document_id}


def pipeline_factory(*, sleep_seconds: float = 0, omit: str | None = None,
                     failure: Exception | None = None):
    return lambda config: SuccessfulFakePipeline(
        config, sleep_seconds=sleep_seconds, omit=omit, failure=failure
    )


def test_store_persists_job_events_schema_and_pragmas(tmp_path):
    config = make_config(tmp_path)
    first = submit(config, make_pdf(tmp_path), chat_id="CHAT-1")
    reopened = JobStore(config.effective_job_database_path)
    job = reopened.get_job(first.job.job_id)
    assert job.status is JobStatus.QUEUED
    assert job.stage is JobStage.SUBMITTED
    assert [event.event_type for event in reopened.list_events(job.job_id)] == ["JOB_CREATED"]
    schema = reopened.schema_export()
    assert schema["schema_version"] == 1
    assert schema["pragmas"]["journal_mode"].lower() == "wal"
    assert schema["pragmas"]["foreign_keys"] == 1
    assert {item["name"] for item in schema["objects"]} >= {"jobs", "job_events", "schema_version"}


def test_idempotency_with_chat_id_returns_existing_but_local_without_chat_allows_new(tmp_path):
    config = make_config(tmp_path)
    pdf = make_pdf(tmp_path)
    first = submit(config, pdf, chat_id="CHAT-1")
    duplicate = submit(config, pdf, chat_id="CHAT-1")
    other_chat = submit(config, pdf, chat_id="CHAT-2")
    other_file = submit(config, make_pdf(tmp_path, "other.pdf", b"%PDF-other"), chat_id="CHAT-1")
    local_one = submit(config, pdf)
    local_two = submit(config, pdf)
    assert first.created is True
    assert duplicate.created is False and duplicate.job.job_id == first.job.job_id
    assert other_chat.job.job_id != first.job.job_id
    assert other_file.job.job_id != first.job.job_id
    assert local_one.job.job_id != local_two.job.job_id
    assert local_one.job.output_dir != local_two.job.output_dir

    store = JobStore(config.effective_job_database_path)
    claimed = store.claim_next_job("W1")
    assert claimed and claimed.job_id == first.job.job_id
    store.mark_failed(first.job.job_id, "W1", "PIPELINE_FAILED", "test")
    failed_duplicate = submit(config, pdf, chat_id="CHAT-1")
    assert failed_duplicate.created is False
    assert failed_duplicate.job.status is JobStatus.FAILED


def test_atomic_claim_allows_only_one_of_two_workers(tmp_path):
    config = make_config(tmp_path)
    job = submit(config, make_pdf(tmp_path)).job
    store = JobStore(config.effective_job_database_path)
    barrier = threading.Barrier(2)

    def claim(worker_id: str):
        barrier.wait()
        return store.claim_next_job(worker_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(claim, ("W1", "W2")))
    winners = [item for item in claimed if item is not None]
    assert len(winners) == 1
    assert winners[0].job_id == job.job_id
    assert store.get_job(job.job_id).status is JobStatus.RUNNING


def test_stage_and_heartbeat_require_current_worker_ownership(tmp_path):
    config = make_config(tmp_path)
    job = submit(config, make_pdf(tmp_path)).job
    store = JobStore(config.effective_job_database_path)
    claimed = store.claim_next_job("W1")
    assert claimed and claimed.attempt_count == 1
    updated = store.update_stage(job.job_id, "W1", JobStage.PARSER)
    before = updated.heartbeat_at
    time.sleep(0.01)
    assert store.update_heartbeat(job.job_id, "W1") is True
    assert store.get_job(job.job_id).heartbeat_at != before
    assert store.update_heartbeat(job.job_id, "OTHER") is False
    with pytest.raises(JobStateError):
        store.update_stage(job.job_id, "OTHER", JobStage.CHUNK)


def test_stale_running_is_interrupted_then_explicitly_requeued(tmp_path):
    config = make_config(tmp_path)
    job = submit(config, make_pdf(tmp_path)).job
    store = JobStore(config.effective_job_database_path)
    store.claim_next_job("W1")
    old = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("UPDATE jobs SET heartbeat_at=? WHERE job_id=?", (old, job.job_id))
    assert store.recover_stale_running_jobs(10) == [job.job_id]
    interrupted = store.get_job(job.job_id)
    assert interrupted.status is JobStatus.INTERRUPTED
    requeued = store.requeue_job(job.job_id)
    assert requeued.status is JobStatus.QUEUED
    assert requeued.attempt_count == 1
    assert [event.event_type for event in store.list_events(job.job_id)] == [
        "JOB_CREATED", "JOB_CLAIMED", "JOB_INTERRUPTED", "JOB_REQUEUED"
    ]


def test_attempt_count_and_max_attempts_are_enforced(tmp_path):
    config = make_config(tmp_path, max_attempts=2)
    job = submit(config, make_pdf(tmp_path)).job
    store = JobStore(config.effective_job_database_path)
    assert store.claim_next_job("W1").attempt_count == 1
    store.mark_failed(job.job_id, "W1", "PIPELINE_FAILED", "first")
    store.requeue_job(job.job_id)
    assert store.claim_next_job("W2").attempt_count == 2
    store.mark_failed(job.job_id, "W2", "PIPELINE_FAILED", "second")
    with pytest.raises(JobStateError, match="max_attempts"):
        store.requeue_job(job.job_id)


def test_worker_success_validates_results_and_records_all_stages(tmp_path):
    config = make_config(tmp_path)
    job = submit(config, make_pdf(tmp_path)).job
    worker = JobWorker(config, worker_id="W1", pipeline_factory=pipeline_factory())
    result = worker.run_once()
    assert result and result.status is JobStatus.COMPLETED
    assert result.stage is JobStage.DONE
    assert result.result_path == str(Path(job.output_dir, "client_api_response.json").resolve())
    stages = [event.stage for event in worker.store.list_events(job.job_id) if event.event_type == "STAGE_CHANGED"]
    assert stages == [
        JobStage.INPUT_CHECK, JobStage.DOCLING, JobStage.PARSER, JobStage.CHUNK,
        JobStage.RETRIEVAL, JobStage.JUDGE, JobStage.AGGREGATION, JobStage.CLIENT_OUTPUT,
    ]


def test_heartbeat_updates_while_pipeline_is_blocked(tmp_path, monkeypatch):
    config = make_config(tmp_path, heartbeat=0.01)
    submit(config, make_pdf(tmp_path))
    worker = JobWorker(config, worker_id="W1", pipeline_factory=pipeline_factory(sleep_seconds=0.08))
    calls: list[float] = []
    original = worker.store.update_heartbeat

    def counting(job_id, worker_id):
        calls.append(time.monotonic())
        return original(job_id, worker_id)

    monkeypatch.setattr(worker.store, "update_heartbeat", counting)
    assert worker.run_once().status is JobStatus.COMPLETED
    assert len(calls) >= 2


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("missing", "JOB_INPUT_MISSING"),
        ("changed", "JOB_INPUT_CHANGED"),
    ],
)
def test_worker_rejects_missing_or_changed_input(tmp_path, mutation, expected_code):
    config = make_config(tmp_path)
    pdf = make_pdf(tmp_path)
    job = submit(config, pdf).job
    if mutation == "missing":
        pdf.unlink()
    else:
        pdf.write_bytes(b"%PDF-changed")
    result = JobWorker(config, worker_id="W1", pipeline_factory=pipeline_factory()).run_once()
    assert result.status is JobStatus.FAILED
    assert result.error_code == expected_code
    assert not Path(job.output_dir).exists()


def test_worker_missing_required_result_cannot_complete(tmp_path):
    config = make_config(tmp_path)
    submit(config, make_pdf(tmp_path))
    worker = JobWorker(
        config,
        worker_id="W1",
        pipeline_factory=pipeline_factory(omit="client_api_response.json"),
    )
    result = worker.run_once()
    assert result.status is JobStatus.FAILED
    assert result.error_code == "RESULT_MISSING"


def test_pipeline_exception_and_known_aggregation_code_are_persisted_safely(tmp_path):
    config = make_config(tmp_path)
    job = submit(config, make_pdf(tmp_path)).job
    failure = RuntimeError("AGGREGATION_EVIDENCE_UNMAPPED api_key=supersecret")
    worker = JobWorker(config, worker_id="W1", pipeline_factory=pipeline_factory(failure=failure))
    result = worker.run_once()
    assert result.status is JobStatus.FAILED
    assert result.error_code == "AGGREGATION_EVIDENCE_UNMAPPED"
    assert "supersecret" not in (result.error_message or "")
    assert b"supersecret" not in config.effective_job_database_path.read_bytes()
    assert worker.store.list_events(job.job_id)[-1].event_type == "JOB_FAILED"


def test_illegal_transitions_are_rejected(tmp_path):
    config = make_config(tmp_path)
    job = submit(config, make_pdf(tmp_path)).job
    store = JobStore(config.effective_job_database_path)
    with pytest.raises(JobStateError):
        store.requeue_job(job.job_id)

    claimed = store.claim_next_job("W1")
    assert claimed is not None
    store.update_stage(job.job_id, "W1", JobStage.PARSER)
    with pytest.raises(JobStateError, match="stage regression"):
        store.update_stage(job.job_id, "W1", JobStage.DOCLING)
    interrupted = store.mark_interrupted(job.job_id, "W1", "controlled stop")
    assert interrupted.status is JobStatus.INTERRUPTED


def test_job_cli_submit_status_result_and_requeue_are_metadata_only(tmp_path, monkeypatch, capsys):
    config = make_config(tmp_path)
    pdf = make_pdf(tmp_path)
    monkeypatch.setattr("review_pipeline.job_cli.load_config", lambda _: config)
    monkeypatch.setattr(sys, "argv", ["job_cli", "submit", "--pdf", str(pdf), "--chat-id", "C1"])
    job_cli_main()
    submitted = json.loads(capsys.readouterr().out)
    assert submitted["created"] is True and submitted["api_calls"] == 0
    job_id = submitted["job"]["job_id"]

    monkeypatch.setattr(sys, "argv", ["job_cli", "status", job_id])
    job_cli_main()
    assert json.loads(capsys.readouterr().out)["status"] == "QUEUED"

    monkeypatch.setattr(sys, "argv", ["job_cli", "result", job_id])
    job_cli_main()
    assert json.loads(capsys.readouterr().out)["result_path"] is None


def test_default_config_keeps_frozen_business_values_and_adds_job_runtime():
    config_path = Path(__file__).resolve().parents[1] / "config" / "pipeline.local.yaml"
    config = load_config(config_path)
    assert config.judge_settings["model"] == "qwen3-8b"
    assert config.judge_settings["max_tokens"] == 1024
    assert config.client_pass_evidence_top_n == 3
    assert config.enabled_review_points == (RULE_ID,)
    assert config.effective_job_database_path.name == "jobs.sqlite3"
    assert config.job_max_attempts == 2
    assert int(config.job_settings["worker_concurrency"]) == 1


def test_orchestrator_optional_progress_hook_preserves_stage_order_without_real_calls(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    pdf = make_pdf(tmp_path)
    orchestrator = PipelineOrchestrator(config)

    def fingerprint(_):
        return "stage-fingerprint"

    monkeypatch.setattr(orchestrator, "_stage_fingerprint", fingerprint)

    def input_stage(source, target, digest):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        return {"source_pdf": str(target)}, {}, {"status": "PASS"}

    def docling_stage(source, output):
        (output / "document.json").write_text("{}", encoding="utf-8")
        return {}, {}, {"status": "PASS"}

    def parser_stage(pdf_path, document_json, output, resume):
        for name in ("page_blocks.jsonl", "good.md", "source_map.jsonl", "final_page_structures.jsonl"):
            (output / name).write_text("{}\n", encoding="utf-8")
        return {}, {}, {"status": "PASS"}

    def chunk_stage(document_id, parser_dir, output):
        (output / "chunks.jsonl").write_text("{}\n", encoding="utf-8")
        return {}, {}, {"status": "PASS"}

    def retrieval_stage(chunks, output, rule_id):
        (output / "judge_inputs.jsonl").write_text("{}\n", encoding="utf-8")
        (output / "retrieval_candidates.jsonl").write_text("{}\n", encoding="utf-8")
        return {}, {}, {"status": "PASS"}

    def judge_stage(chunks, inputs, output):
        for name in ("predictions.jsonl", "validated_predictions.jsonl"):
            (output / name).write_text("{}\n", encoding="utf-8")
        (output / "document_review_result.json").write_text("{}", encoding="utf-8")
        return {}, {}, {"status": "PASS"}

    monkeypatch.setattr(orchestrator, "_input_stage", input_stage)
    monkeypatch.setattr(orchestrator, "_docling_stage", docling_stage)
    monkeypatch.setattr(orchestrator, "_parser_stage", parser_stage)
    monkeypatch.setattr(orchestrator, "_chunk_stage", chunk_stage)
    monkeypatch.setattr(orchestrator, "_retrieval_stage", retrieval_stage)
    monkeypatch.setattr(orchestrator, "_judge_stage", judge_stage)
    stages: list[str] = []
    result = orchestrator.run_document(
        pdf, "DOC_TEST", RULE_ID, resume=False, stop_after="judge", on_stage_change=stages.append
    )
    assert result["pipeline_status"] == "STOPPED"
    assert stages == ["input", "docling", "parser", "chunk", "retrieval", "judge"]
