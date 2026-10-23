from __future__ import annotations

import asyncio
import copy
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

import pytest
from fastapi.testclient import TestClient

from review_pipeline.config import PipelineConfig, load_config
from review_pipeline.http.app import create_app
from review_pipeline.http.input_resolver import (ResolvedInput,
                                                 UnsupportedFileUrlResolver)
from review_pipeline.http.errors import ReviewHTTPError
from review_pipeline.http.sync_service import SyncJobWaiter
from review_pipeline.job_models import JobStage, JobStatus
from review_pipeline.job_store import JobStore


RULE_ID = "RP_REGION_INDUSTRY_AWARD"


def make_config(tmp_path: Path) -> PipelineConfig:
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
    config_path = tmp_path / "pipeline.yaml"
    config_path.write_text("test: true\n", encoding="utf-8")
    parser_env = tmp_path / "parser.env"
    judge_env = tmp_path / "judge.env"
    parser_env.write_text("", encoding="utf-8")
    judge_env.write_text("", encoding="utf-8")
    py = Path(sys.executable)
    return PipelineConfig(
        config_path=config_path,
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
        client_settings={"pass_evidence_top_n": 3},
        enabled_review_points=(RULE_ID,),
        job_database_path=tmp_path / "state" / "jobs.sqlite3",
        job_settings={
            "poll_interval_seconds": 0.01,
            "heartbeat_interval_seconds": 0.02,
            "stale_after_seconds": 1,
            "max_attempts": 2,
            "worker_concurrency": 1,
            "busy_timeout_ms": 5000,
        },
        http_settings={
            "host": "127.0.0.1",
            "port": 8000,
            "api_prefix": "/api/v1",
            "docs_enabled": True,
        },
        file_url_settings={
            "enabled": True,
            "allowed_schemes": ["http", "https"],
            "allow_private_network": False,
            "trusted_private_hosts": [],
            "trusted_private_cidrs": [],
            "max_redirects": 3,
            "connect_timeout_seconds": 10,
            "read_timeout_seconds": 60,
            "write_timeout_seconds": 10,
            "pool_timeout_seconds": 10,
            "max_download_bytes": 67108864,
            "chunk_size_bytes": 65536,
        },
        client_sync_settings={
            "enabled": True,
            "route_path": "/api/v1/review",
            "wait_timeout_seconds": 1800,
            "poll_interval_seconds": 2,
        },
    )


class FakeInputResolver:
    def __init__(self, mapping: dict[str, Path]):
        self.mapping = mapping
        self.calls: list[str] = []

    def resolve(self, request):
        self.calls.append(request.file_url)
        path = self.mapping[request.file_url]
        return ResolvedInput(path, request.file_url, request.file_name)


def request_body(*, chat_id: str = "CHAT-001", stream: bool = False,
                 items: list[dict] | None = None) -> dict:
    return {
        "chatId": chat_id,
        "stream": stream,
        "detail": False,
        "variables": {"source": "offline-test"},
        "messages": [{
            "role": "user",
            "content": items if items is not None else [{
                "type": "file_url",
                "name": "sample.pdf",
                "url": "https://example.test/sample.pdf",
            }],
        }],
    }


def make_client(tmp_path: Path, *, default_resolver: bool = False):
    config = make_config(tmp_path)
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-offline-http-test")
    resolver = UnsupportedFileUrlResolver() if default_resolver else FakeInputResolver({
        "https://example.test/sample.pdf": pdf,
    })
    store = JobStore(config.effective_job_database_path)
    app = create_app(config, job_store=store, input_resolver=resolver)
    return TestClient(app), app, store, resolver


def submit_job(client: TestClient, chat_id: str = "CHAT-001") -> dict:
    response = client.post("/api/v1/review/jobs", json=request_body(chat_id=chat_id))
    assert response.status_code == 202
    return response.json()


def test_health_checks_configuration_and_sqlite_without_starting_worker(tmp_path):
    client, app, _, resolver = make_client(tmp_path)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok", "service": "tender-review", "config": "ok", "job_store": "ok"
    }
    assert app.state.worker_started is False
    assert resolver.calls == []


@pytest.mark.parametrize("body", [
    {"stream": False, "detail": False, "variables": {}, "messages": []},
    request_body(chat_id="   "),
])
def test_chat_id_validation_uses_uniform_error(body, tmp_path):
    client, _, _, _ = make_client(tmp_path)
    response = client.post("/api/v1/review/jobs", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"


def test_stream_true_is_explicitly_rejected(tmp_path):
    client, _, _, _ = make_client(tmp_path)
    response = client.post("/api/v1/review/jobs", json=request_body(stream=True))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "STREAM_NOT_SUPPORTED"


@pytest.mark.parametrize(
    ("items", "code"),
    [
        ([{"type": "text", "name": "note"}], "FILE_URL_MISSING"),
        ([
            {"type": "file_url", "url": "https://example.test/a.pdf"},
            {"type": "file_url", "url": "https://example.test/b.pdf"},
        ], "MULTIPLE_FILE_URLS_NOT_SUPPORTED"),
        ([{"type": "file_url", "url": ""}], "FILE_URL_INVALID"),
        ([{"type": "file_url", "url": "file:///tmp/a.pdf"}], "FILE_URL_INVALID"),
        ([{"type": "file_url", "url": "ftp://example.test/a.pdf"}], "FILE_URL_SCHEME_NOT_ALLOWED"),
        ([{"type": "file_url", "url": "https://user:pass@example.test/a.pdf"}],
         "FILE_URL_CREDENTIALS_NOT_ALLOWED"),
    ],
)
def test_file_url_contract_errors(items, code, tmp_path):
    client, _, _, _ = make_client(tmp_path)
    response = client.post("/api/v1/review/jobs", json=request_body(items=items))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code


def test_explicitly_disabled_resolver_returns_501_without_network_access(tmp_path):
    client, _, store, _ = make_client(tmp_path, default_resolver=True)
    response = client.post("/api/v1/review/jobs", json=request_body())
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "FILE_URL_RESOLUTION_NOT_IMPLEMENTED"
    with sqlite3_connection(store.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


class sqlite3_connection:
    def __init__(self, path: Path):
        import sqlite3
        self.connection = sqlite3.connect(path)

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc, traceback):
        self.connection.close()


def test_fake_resolver_creates_job_preserves_chat_id_and_is_idempotent(tmp_path, monkeypatch):
    from review_pipeline.orchestrator import PipelineOrchestrator

    monkeypatch.setattr(
        PipelineOrchestrator,
        "run_document",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("HTTP must not run Pipeline")),
    )
    client, _, store, resolver = make_client(tmp_path)
    first = submit_job(client, "CHAT-IDEMPOTENT")
    second = submit_job(client, "CHAT-IDEMPOTENT")
    assert first["job_id"] == second["job_id"]
    assert first["created"] is True and second["created"] is False
    assert first["status"] == "QUEUED" and first["stage"] == "SUBMITTED"
    assert store.get_job(first["job_id"]).chat_id == "CHAT-IDEMPOTENT"
    assert resolver.calls == ["https://example.test/sample.pdf"] * 2


def test_status_returns_only_safe_metadata_and_unknown_job_is_404(tmp_path):
    client, _, _, _ = make_client(tmp_path)
    job = submit_job(client)
    response = client.get(f"/api/v1/review/jobs/{job['job_id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["chat_id"] == "CHAT-001"
    assert body["status"] == "QUEUED"
    rendered = response.text
    assert "source_path" not in rendered
    assert "database_path" not in rendered
    assert str(tmp_path) not in rendered

    missing = client.get("/api/v1/review/jobs/JOB_UNKNOWN")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_queued_and_running_results_are_not_ready(tmp_path):
    client, _, store, _ = make_client(tmp_path)
    job = submit_job(client)
    queued = client.get(f"/api/v1/review/jobs/{job['job_id']}/result")
    assert queued.status_code == 409
    assert queued.json()["error"]["code"] == "RESULT_NOT_READY"
    store.claim_next_job("TEST-WORKER")
    running = client.get(f"/api/v1/review/jobs/{job['job_id']}/result")
    assert running.status_code == 409
    assert running.json()["error"]["code"] == "RESULT_NOT_READY"


@pytest.mark.parametrize("target", ["FAILED", "INTERRUPTED"])
def test_failed_or_interrupted_result_returns_technical_error_only(tmp_path, target):
    client, _, store, _ = make_client(tmp_path)
    job = submit_job(client)
    store.claim_next_job("TEST-WORKER")
    if target == "FAILED":
        store.mark_failed(job["job_id"], "TEST-WORKER", "AGGREGATION_EVIDENCE_UNMAPPED",
                          f"private path {tmp_path} api_key=secret")
        expected = "JOB_FAILED"
    else:
        store.mark_interrupted(job["job_id"], "TEST-WORKER", f"private path {tmp_path}")
        expected = "JOB_INTERRUPTED"
    response = client.get(f"/api/v1/review/jobs/{job['job_id']}/result")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == expected
    assert str(tmp_path) not in response.text
    assert "secret" not in response.text
    assert "is_pass" not in response.text


def client_wrapper() -> tuple[dict, str]:
    content = json.dumps({
        "result": [{
            "term": ["违规条目"],
            "reason": "模型审查原因",
            "text": ["召回原文"],
            "page": [12],
            "is_pass": False,
            "name": "地域/行业业绩奖项及外部荣誉作为招投标加分、中标条件",
            "rule": "规则说明",
            "type": "资质加分审查",
            "index": 12,
        }],
        "suggestions": "删除不合理加分条件",
        "scope": "切分审核",
    }, ensure_ascii=False)
    return ({
        "id": "OLD_INTERNAL_ID",
        "model": "qwen3-8b",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "choices": [{
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
            "index": 0,
        }],
    }, content)


def complete_job(store: JobStore, job_id: str, *, payload: str | None) -> Path:
    job = store.claim_next_job("TEST-WORKER")
    assert job and job.job_id == job_id
    output = Path(job.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / "client_api_response.json"
    if payload is not None:
        result_path.write_text(payload, encoding="utf-8")
    store.mark_completed(job_id, "TEST-WORKER", str(result_path))
    return result_path


def test_completed_result_only_rewrites_top_level_id_and_preserves_content_string(tmp_path):
    client, _, store, _ = make_client(tmp_path)
    job = submit_job(client, "CHAT-FINAL")
    wrapper, original_content = client_wrapper()
    original = copy.deepcopy(wrapper)
    complete_job(store, job["job_id"], payload=json.dumps(wrapper, ensure_ascii=False))
    response = client.get(f"/api/v1/review/jobs/{job['job_id']}/result")
    assert response.status_code == 200
    result = response.json()
    assert result["id"] == "CHAT-FINAL"
    assert isinstance(result["choices"][0]["message"]["content"], str)
    assert result["choices"][0]["message"]["content"] == original_content
    decoded = json.loads(result["choices"][0]["message"]["content"])
    assert len(decoded["result"]) == 1
    assert decoded["result"][0]["index"] == 12
    comparison = copy.deepcopy(result)
    comparison["id"] = original["id"]
    assert comparison == original


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (None, "RESULT_FILE_MISSING"),
        ("not-json", "RESULT_FILE_INVALID"),
        (json.dumps({"choices": []}), "RESULT_FILE_INVALID"),
    ],
)
def test_completed_result_file_errors_are_centralized(payload, code, tmp_path):
    client, _, store, _ = make_client(tmp_path)
    job = submit_job(client)
    complete_job(store, job["job_id"], payload=payload)
    response = client.get(f"/api/v1/review/jobs/{job['job_id']}/result")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == code
    assert str(tmp_path) not in response.text


def test_unexpected_exception_is_generic_and_does_not_leak_traceback_or_secret(tmp_path, monkeypatch):
    client, app, _, _ = make_client(tmp_path)
    monkeypatch.setattr(
        app.state.review_service,
        "health",
        lambda: (_ for _ in ()).throw(RuntimeError(f"api_key=secret {tmp_path}")),
    )
    safe_client = TestClient(app, raise_server_exceptions=False)
    response = safe_client.get("/health")
    assert response.status_code == 500
    assert response.json() == {"error": {
        "code": "INTERNAL_SERVER_ERROR", "message": "Internal server error"
    }}
    assert "secret" not in response.text and str(tmp_path) not in response.text


def test_app_factory_does_not_construct_or_start_worker(tmp_path, monkeypatch):
    from review_pipeline.job_worker import JobWorker

    monkeypatch.setattr(
        JobWorker,
        "__init__",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Worker must remain independent")),
    )
    config = make_config(tmp_path)
    app = create_app(config, input_resolver=FakeInputResolver({}))
    assert app.state.worker_started is False
    assert TestClient(app).get("/health").status_code == 200


def test_default_http_configuration_is_local_only_and_business_defaults_remain_frozen():
    config_path = Path(__file__).resolve().parents[1] / "config" / "pipeline.local.yaml"
    config = load_config(config_path)
    assert config.http_host == "127.0.0.1"
    assert config.http_port == 8000
    assert config.http_api_prefix == "/api/v1"
    assert config.enabled_review_points == (RULE_ID,)
    assert config.judge_settings["model"] == "qwen3-8b"
    assert config.judge_settings["max_tokens"] == 1024
    assert config.client_pass_evidence_top_n == 3
    assert config.file_url_enabled is True
    assert config.file_url_allow_private_network is False
    assert config.file_url_trusted_private_hosts == ()
    assert config.file_url_trusted_private_cidrs == ()
    assert config.file_url_max_download_bytes == 67108864
    assert config.client_sync_enabled is True
    assert config.client_sync_route_path == "/api/v1/review"
    assert config.client_sync_wait_timeout_seconds == 1800
    assert config.client_sync_poll_interval_seconds == 2


class CompleteImmediatelyWaiter:
    def __init__(self, store: JobStore, payload: dict):
        self.store = store
        self.payload = payload
        self.calls: list[tuple[str, str | None]] = []
        self._lock = Lock()

    async def wait(self, job_id: str, *, chat_id: str | None = None):
        self.calls.append((job_id, chat_id))
        with self._lock:
            job = self.store.get_job(job_id)
            if job.status is JobStatus.QUEUED:
                claimed = self.store.claim_next_job("FAKE-WORKER")
                assert claimed and claimed.job_id == job_id
                output = Path(claimed.output_dir)
                output.mkdir(parents=True, exist_ok=True)
                result_path = output / "client_api_response.json"
                result_path.write_text(json.dumps(self.payload, ensure_ascii=False), encoding="utf-8")
                self.store.mark_completed(job_id, "FAKE-WORKER", str(result_path))
        return self.store.get_job(job_id)


def test_sync_route_submits_waits_and_returns_exact_client_contract(tmp_path, monkeypatch):
    from review_pipeline.orchestrator import PipelineOrchestrator

    monkeypatch.setattr(
        PipelineOrchestrator,
        "run_document",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("HTTP must not run Pipeline")),
    )
    config = make_config(tmp_path)
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-sync-test")
    store = JobStore(config.effective_job_database_path)
    payload, expected_content = client_wrapper()
    payload["log_file"] = str(tmp_path / "private.log")
    waiter = CompleteImmediatelyWaiter(store, payload)
    resolver = FakeInputResolver({"https://example.test/sample.pdf": pdf})
    app = create_app(
        config,
        job_store=store,
        input_resolver=resolver,
        sync_waiter=waiter,
    )
    response = TestClient(app).post("/api/v1/review", json=request_body(chat_id="CHAT-SYNC"))
    assert response.status_code == 200
    assert set(response.json()) == {"id", "model", "usage", "choices"}
    assert response.json()["id"] == "CHAT-SYNC"
    assert response.json()["choices"][0]["message"]["content"] == expected_content
    assert response.json()["choices"][0]["message"]["role"] == "assistant"
    assert response.json()["choices"][0]["finish_reason"] == "stop"
    assert response.json()["choices"][0]["index"] == 0
    decoded = json.loads(response.json()["choices"][0]["message"]["content"])
    assert len(decoded["result"]) == 1 and decoded["result"][0]["index"] == 12
    assert "log_file" not in response.text and "job_id" not in response.text
    job = store.get_job(waiter.calls[0][0])
    assert job.status is JobStatus.COMPLETED
    assert job.attempt_count == 1
    assert app.state.worker_started is False
    assert resolver.calls == ["https://example.test/sample.pdf"]


def test_sync_route_reuses_completed_idempotent_job_and_same_result_adapter(tmp_path):
    client, app, store, _ = make_client(tmp_path)
    submitted = submit_job(client, "CHAT-SYNC-REUSE")
    payload, _ = client_wrapper()
    payload["log_file"] = "internal-only.log"
    complete_job(store, submitted["job_id"], payload=json.dumps(payload, ensure_ascii=False))
    sync_result = client.post("/api/v1/review", json=request_body(chat_id="CHAT-SYNC-REUSE"))
    async_result = client.get(f"/api/v1/review/jobs/{submitted['job_id']}/result")
    assert sync_result.status_code == 200
    assert sync_result.json() == async_result.json()
    assert set(sync_result.json()) == {"id", "model", "usage", "choices"}
    assert len(store.list_events(submitted["job_id"])) == 3
    assert app.state.worker_started is False


@pytest.mark.parametrize(
    ("terminal", "expected_code"),
    [("FAILED", "JOB_FAILED"), ("INTERRUPTED", "JOB_INTERRUPTED")],
)
def test_sync_waiter_maps_terminal_failures_without_leaking_internal_details(
        tmp_path, terminal, expected_code):
    client, _, store, _ = make_client(tmp_path)
    submitted = submit_job(client, f"CHAT-{terminal}")
    store.claim_next_job("TEST-WORKER")
    if terminal == "FAILED":
        store.mark_failed(submitted["job_id"], "TEST-WORKER", "PRIVATE_FAILURE",
                          f"secret path {tmp_path}")
    else:
        store.mark_interrupted(submitted["job_id"], "TEST-WORKER", f"secret {tmp_path}")
    waiter = SyncJobWaiter(store, timeout_seconds=1, poll_interval_seconds=0.01)
    with pytest.raises(ReviewHTTPError) as raised:
        asyncio.run(waiter.wait(submitted["job_id"], chat_id=f"CHAT-{terminal}"))
    assert raised.value.status_code == 500
    assert raised.value.code == expected_code
    assert raised.value.job_id is None
    assert str(tmp_path) not in raised.value.message


def test_sync_waiter_timeout_is_read_only_and_does_not_claim_or_mutate_job(tmp_path):
    client, _, store, _ = make_client(tmp_path)
    submitted = submit_job(client, "CHAT-TIMEOUT")
    before = store.get_job(submitted["job_id"])
    before_events = store.list_events(submitted["job_id"])
    clock = iter([0.0, 0.0, 1.0])

    async def no_sleep(_seconds: float) -> None:
        return None

    waiter = SyncJobWaiter(
        store,
        timeout_seconds=0.5,
        poll_interval_seconds=0.1,
        sleep=no_sleep,
        monotonic=lambda: next(clock),
    )
    with pytest.raises(ReviewHTTPError) as raised:
        asyncio.run(waiter.wait(submitted["job_id"], chat_id="CHAT-TIMEOUT"))
    assert raised.value.status_code == 504
    assert raised.value.code == "SYNC_WAIT_TIMEOUT"
    after = store.get_job(submitted["job_id"])
    assert after.status is before.status is JobStatus.QUEUED
    assert after.attempt_count == before.attempt_count == 0
    assert after.worker_id is before.worker_id is None
    assert store.list_events(submitted["job_id"]) == before_events


def test_sync_waiter_observes_queued_running_completed_transitions(tmp_path):
    client, _, store, _ = make_client(tmp_path)
    submitted = submit_job(client, "CHAT-TRANSITIONS")
    payload, _ = client_wrapper()
    step = 0

    async def advance(_seconds: float) -> None:
        nonlocal step
        step += 1
        if step == 1:
            store.claim_next_job("REAL-INDEPENDENT-WORKER")
        elif step == 2:
            job = store.get_job(submitted["job_id"])
            output = Path(job.output_dir)
            output.mkdir(parents=True, exist_ok=True)
            result = output / "client_api_response.json"
            result.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            store.mark_completed(job.job_id, "REAL-INDEPENDENT-WORKER", str(result))

    waiter = SyncJobWaiter(store, timeout_seconds=10, poll_interval_seconds=0.01, sleep=advance)
    completed = asyncio.run(waiter.wait(submitted["job_id"], chat_id="CHAT-TRANSITIONS"))
    assert completed.status is JobStatus.COMPLETED
    assert completed.attempt_count == 1
    assert [event.event_type for event in store.list_events(completed.job_id)] == [
        "JOB_CREATED", "JOB_CLAIMED", "JOB_COMPLETED"
    ]


def test_sync_wait_cancellation_does_not_change_persistent_job(tmp_path):
    client, _, store, _ = make_client(tmp_path)
    submitted = submit_job(client, "CHAT-CANCEL")
    entered_sleep = asyncio.Event()
    release_sleep = asyncio.Event()

    async def blocked_sleep(_seconds: float) -> None:
        entered_sleep.set()
        await release_sleep.wait()

    async def run_and_cancel() -> None:
        waiter = SyncJobWaiter(store, timeout_seconds=10, poll_interval_seconds=1,
                               sleep=blocked_sleep)
        task = asyncio.create_task(waiter.wait(submitted["job_id"], chat_id="CHAT-CANCEL"))
        await entered_sleep.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_and_cancel())
    job = store.get_job(submitted["job_id"])
    assert job.status is JobStatus.QUEUED
    assert job.attempt_count == 0
    assert [event.event_type for event in store.list_events(job.job_id)] == ["JOB_CREATED"]


def test_sync_route_validation_and_result_file_errors_remain_centralized(tmp_path):
    client, _, store, _ = make_client(tmp_path)
    invalid_request = client.post("/api/v1/review", json=request_body(stream=True))
    assert invalid_request.status_code == 400
    assert invalid_request.json()["error"]["code"] == "STREAM_NOT_SUPPORTED"

    submitted = submit_job(client, "CHAT-BAD-RESULT")
    complete_job(store, submitted["job_id"], payload="not-json")
    invalid_result = client.post("/api/v1/review", json=request_body(chat_id="CHAT-BAD-RESULT"))
    assert invalid_result.status_code == 500
    assert invalid_result.json()["error"]["code"] == "RESULT_FILE_INVALID"
    assert str(tmp_path) not in invalid_result.text

    missing = submit_job(client, "CHAT-MISSING-RESULT")
    complete_job(store, missing["job_id"], payload=None)
    missing_result = client.post(
        "/api/v1/review", json=request_body(chat_id="CHAT-MISSING-RESULT")
    )
    assert missing_result.status_code == 500
    assert missing_result.json()["error"]["code"] == "RESULT_FILE_MISSING"


def test_concurrent_identical_sync_requests_reuse_one_persistent_job(tmp_path):
    config = make_config(tmp_path)
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-concurrent-sync")
    store = JobStore(config.effective_job_database_path)
    payload, _ = client_wrapper()
    waiter = CompleteImmediatelyWaiter(store, payload)
    app = create_app(
        config,
        job_store=store,
        input_resolver=FakeInputResolver({"https://example.test/sample.pdf": pdf}),
        sync_waiter=waiter,
    )

    def send() -> dict:
        response = TestClient(app).post(
            "/api/v1/review", json=request_body(chat_id="CHAT-CONCURRENT")
        )
        assert response.status_code == 200
        return response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _value: send(), range(2)))
    assert results[0] == results[1]
    with sqlite3_connection(store.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_different_chat_ids_create_distinct_sync_jobs(tmp_path):
    config = make_config(tmp_path)
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-chat-id-sync")
    store = JobStore(config.effective_job_database_path)
    payload, _ = client_wrapper()
    waiter = CompleteImmediatelyWaiter(store, payload)
    client = TestClient(create_app(
        config,
        job_store=store,
        input_resolver=FakeInputResolver({"https://example.test/sample.pdf": pdf}),
        sync_waiter=waiter,
    ))
    first = client.post("/api/v1/review", json=request_body(chat_id="CHAT-A"))
    second = client.post("/api/v1/review", json=request_body(chat_id="CHAT-B"))
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == "CHAT-A" and second.json()["id"] == "CHAT-B"
    with sqlite3_connection(store.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2


def test_sync_waiter_uses_configured_timeout_and_poll_interval(tmp_path):
    config = make_config(tmp_path)
    settings = dict(config.client_sync_settings)
    settings.update({"wait_timeout_seconds": 7.5, "poll_interval_seconds": 0.25})
    object.__setattr__(config, "client_sync_settings", settings)
    app = create_app(config, input_resolver=FakeInputResolver({}))
    waiter = app.state.sync_review_service.waiter
    assert waiter.timeout_seconds == 7.5
    assert waiter.poll_interval_seconds == 0.25
