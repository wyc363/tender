from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from .job_models import (JOB_STAGE_POSITION, JobEvent, JobRecord, JobStage,
                         JobStateError, JobStatus, require_transition)
from .utils import now_iso, redact


SCHEMA_VERSION = 1


class JobNotFoundError(KeyError):
    pass


class JobStore:
    def __init__(self, database_path: str | Path, *, busy_timeout_ms: int = 5000):
        self.database_path = Path(database_path).expanduser().resolve()
        self.busy_timeout_ms = int(busy_timeout_ms)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=max(self.busy_timeout_ms / 1000, 0.001),
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._transaction(immediate=True) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER NOT NULL,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    chat_id TEXT,
                    source_type TEXT NOT NULL CHECK (source_type = 'LOCAL_FILE'),
                    source_path TEXT NOT NULL,
                    file_sha256 TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    rule_id TEXT NOT NULL,
                    enabled_review_points_json TEXT NOT NULL,
                    idempotency_key TEXT,
                    status TEXT NOT NULL CHECK (status IN ('QUEUED','RUNNING','COMPLETED','FAILED','INTERRUPTED')),
                    stage TEXT NOT NULL CHECK (stage IN ('SUBMITTED','INPUT_CHECK','DOCLING','PARSER','CHUNK','RETRIEVAL','JUDGE','AGGREGATION','CLIENT_OUTPUT','DONE')),
                    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
                    max_attempts INTEGER NOT NULL CHECK (max_attempts >= 1),
                    worker_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    heartbeat_at TEXT,
                    output_dir TEXT NOT NULL,
                    result_path TEXT,
                    error_code TEXT,
                    error_message TEXT
                );

                CREATE UNIQUE INDEX IF NOT EXISTS ux_jobs_idempotency_key
                    ON jobs(idempotency_key) WHERE idempotency_key IS NOT NULL;
                CREATE INDEX IF NOT EXISTS ix_jobs_claim
                    ON jobs(status, created_at, job_id);
                CREATE INDEX IF NOT EXISTS ix_jobs_heartbeat
                    ON jobs(status, heartbeat_at);

                CREATE TABLE IF NOT EXISTS job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    message TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS ix_job_events_job
                    ON job_events(job_id, id);
                """
            )
            current = connection.execute("SELECT MAX(version) AS version FROM schema_version").fetchone()["version"]
            if current is None:
                connection.execute(
                    "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
                    (SCHEMA_VERSION, now_iso()),
                )
            elif int(current) != SCHEMA_VERSION:
                raise RuntimeError(f"Unsupported job database schema version: {current}")

    @staticmethod
    def _append_event_tx(connection: sqlite3.Connection, job_id: str, event_type: str,
                         status: JobStatus, stage: JobStage, message: str | None = None) -> None:
        safe_message = redact(str(message))[:1000] if message else None
        connection.execute(
            "INSERT INTO job_events(job_id,event_type,status,stage,message,created_at) VALUES (?,?,?,?,?,?)",
            (job_id, event_type, status.value, stage.value, safe_message, now_iso()),
        )

    def create_job(self, *, job_id: str, chat_id: str | None, source_path: str,
                   file_sha256: str, document_id: str, rule_id: str,
                   enabled_review_points: tuple[str, ...], idempotency_key: str | None,
                   output_dir: str, max_attempts: int) -> tuple[JobRecord, bool]:
        now = now_iso()
        with self._transaction(immediate=True) as connection:
            if idempotency_key:
                existing = connection.execute(
                    "SELECT * FROM jobs WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
                if existing:
                    return JobRecord.from_row(existing), False
            try:
                connection.execute(
                    """
                    INSERT INTO jobs(
                        job_id,chat_id,source_type,source_path,file_sha256,document_id,rule_id,
                        enabled_review_points_json,idempotency_key,status,stage,attempt_count,max_attempts,
                        worker_id,created_at,updated_at,started_at,finished_at,heartbeat_at,
                        output_dir,result_path,error_code,error_message
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (job_id, chat_id, "LOCAL_FILE", source_path, file_sha256, document_id, rule_id,
                     json.dumps(list(enabled_review_points), ensure_ascii=False), idempotency_key,
                     JobStatus.QUEUED.value, JobStage.SUBMITTED.value, 0, int(max_attempts),
                     None, now, now, None, None, None, output_dir, None, None, None),
                )
            except sqlite3.IntegrityError:
                if not idempotency_key:
                    raise
                existing = connection.execute(
                    "SELECT * FROM jobs WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
                if not existing:
                    raise
                return JobRecord.from_row(existing), False
            self._append_event_tx(connection, job_id, "JOB_CREATED", JobStatus.QUEUED, JobStage.SUBMITTED)
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            return JobRecord.from_row(row), True

    def get_job(self, job_id: str) -> JobRecord:
        with self._read_connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            raise JobNotFoundError(job_id)
        return JobRecord.from_row(row)

    def find_by_idempotency_key(self, key: str) -> JobRecord | None:
        with self._read_connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE idempotency_key = ?", (key,)).fetchone()
        return JobRecord.from_row(row) if row else None

    def list_events(self, job_id: str) -> list[JobEvent]:
        self.get_job(job_id)
        with self._read_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM job_events WHERE job_id = ? ORDER BY id", (job_id,)
            ).fetchall()
        return [JobEvent.from_row(row) for row in rows]

    def claim_next_job(self, worker_id: str) -> JobRecord | None:
        now = now_iso()
        with self._transaction(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = ? AND attempt_count < max_attempts
                ORDER BY created_at, job_id LIMIT 1
                """,
                (JobStatus.QUEUED.value,),
            ).fetchone()
            if not row:
                return None
            require_transition(row["status"], JobStatus.RUNNING)
            updated = connection.execute(
                """
                UPDATE jobs SET status=?,stage=?,attempt_count=attempt_count+1,worker_id=?,
                    started_at=COALESCE(started_at,?),finished_at=NULL,heartbeat_at=?,updated_at=?,
                    error_code=NULL,error_message=NULL
                WHERE job_id=? AND status=?
                """,
                (JobStatus.RUNNING.value, JobStage.INPUT_CHECK.value, worker_id,
                 now, now, now, row["job_id"], JobStatus.QUEUED.value),
            )
            if updated.rowcount != 1:
                return None
            self._append_event_tx(connection, row["job_id"], "JOB_CLAIMED",
                                  JobStatus.RUNNING, JobStage.INPUT_CHECK, f"worker_id={worker_id}")
            claimed = connection.execute("SELECT * FROM jobs WHERE job_id=?", (row["job_id"],)).fetchone()
            return JobRecord.from_row(claimed)

    def _require_owned_running(self, connection: sqlite3.Connection, job_id: str,
                               worker_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            raise JobNotFoundError(job_id)
        if row["status"] != JobStatus.RUNNING.value or row["worker_id"] != worker_id:
            raise JobStateError(f"Job {job_id} is not RUNNING under worker {worker_id}")
        return row

    def update_stage(self, job_id: str, worker_id: str, stage: JobStage | str,
                     message: str | None = None) -> JobRecord:
        target = JobStage(stage)
        now = now_iso()
        with self._transaction(immediate=True) as connection:
            row = self._require_owned_running(connection, job_id, worker_id)
            current = JobStage(row["stage"])
            if JOB_STAGE_POSITION[target] < JOB_STAGE_POSITION[current]:
                raise JobStateError(
                    f"Illegal job stage regression: {current.value} -> {target.value}"
                )
            connection.execute(
                "UPDATE jobs SET stage=?,updated_at=? WHERE job_id=?",
                (target.value, now, job_id),
            )
            self._append_event_tx(connection, job_id, "STAGE_CHANGED",
                                  JobStatus.RUNNING, target, message)
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            return JobRecord.from_row(row)

    def update_heartbeat(self, job_id: str, worker_id: str) -> bool:
        now = now_iso()
        with self._transaction(immediate=True) as connection:
            updated = connection.execute(
                """
                UPDATE jobs SET heartbeat_at=?,updated_at=?
                WHERE job_id=? AND status=? AND worker_id=?
                """,
                (now, now, job_id, JobStatus.RUNNING.value, worker_id),
            )
            return updated.rowcount == 1

    def mark_completed(self, job_id: str, worker_id: str, result_path: str) -> JobRecord:
        now = now_iso()
        with self._transaction(immediate=True) as connection:
            row = self._require_owned_running(connection, job_id, worker_id)
            require_transition(row["status"], JobStatus.COMPLETED)
            connection.execute(
                """
                UPDATE jobs SET status=?,stage=?,result_path=?,finished_at=?,heartbeat_at=?,
                    updated_at=?,error_code=NULL,error_message=NULL WHERE job_id=?
                """,
                (JobStatus.COMPLETED.value, JobStage.DONE.value, result_path,
                 now, now, now, job_id),
            )
            self._append_event_tx(connection, job_id, "JOB_COMPLETED",
                                  JobStatus.COMPLETED, JobStage.DONE)
            return JobRecord.from_row(connection.execute(
                "SELECT * FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone())

    def mark_interrupted(self, job_id: str, worker_id: str,
                         message: str = "Worker interrupted") -> JobRecord:
        now = now_iso()
        safe_message = redact(str(message))[:1000]
        with self._transaction(immediate=True) as connection:
            row = self._require_owned_running(connection, job_id, worker_id)
            require_transition(row["status"], JobStatus.INTERRUPTED)
            stage = JobStage(row["stage"])
            connection.execute(
                """
                UPDATE jobs SET status=?,worker_id=NULL,finished_at=?,updated_at=?,
                    error_code=?,error_message=? WHERE job_id=?
                """,
                (JobStatus.INTERRUPTED.value, now, now, "JOB_INTERRUPTED",
                 safe_message, job_id),
            )
            self._append_event_tx(connection, job_id, "JOB_INTERRUPTED",
                                  JobStatus.INTERRUPTED, stage, safe_message)
            return JobRecord.from_row(connection.execute(
                "SELECT * FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone())

    def mark_failed(self, job_id: str, worker_id: str, error_code: str,
                    error_message: str) -> JobRecord:
        now = now_iso()
        safe_message = redact(str(error_message))[:1000]
        with self._transaction(immediate=True) as connection:
            row = self._require_owned_running(connection, job_id, worker_id)
            require_transition(row["status"], JobStatus.FAILED)
            stage = JobStage(row["stage"])
            connection.execute(
                """
                UPDATE jobs SET status=?,finished_at=?,heartbeat_at=?,updated_at=?,
                    error_code=?,error_message=? WHERE job_id=?
                """,
                (JobStatus.FAILED.value, now, now, now, error_code, safe_message, job_id),
            )
            self._append_event_tx(connection, job_id, "JOB_FAILED",
                                  JobStatus.FAILED, stage, f"{error_code}: {safe_message}")
            return JobRecord.from_row(connection.execute(
                "SELECT * FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone())

    def recover_stale_running_jobs(self, stale_after_seconds: float) -> list[str]:
        cutoff = (datetime.now(UTC) - timedelta(seconds=float(stale_after_seconds))).isoformat()
        interrupted: list[str] = []
        with self._transaction(immediate=True) as connection:
            rows = connection.execute(
                """
                SELECT * FROM jobs WHERE status=?
                  AND COALESCE(heartbeat_at, started_at, updated_at) < ?
                ORDER BY created_at, job_id
                """,
                (JobStatus.RUNNING.value, cutoff),
            ).fetchall()
            for row in rows:
                require_transition(row["status"], JobStatus.INTERRUPTED)
                now = now_iso()
                connection.execute(
                    """
                    UPDATE jobs SET status=?,worker_id=NULL,finished_at=?,updated_at=?,
                        error_code=?,error_message=? WHERE job_id=? AND status=?
                    """,
                    (JobStatus.INTERRUPTED.value, now, now, "JOB_HEARTBEAT_STALE",
                     "Worker heartbeat became stale", row["job_id"], JobStatus.RUNNING.value),
                )
                self._append_event_tx(connection, row["job_id"], "JOB_INTERRUPTED",
                                      JobStatus.INTERRUPTED, JobStage(row["stage"]),
                                      "Worker heartbeat became stale")
                interrupted.append(row["job_id"])
        return interrupted

    def requeue_job(self, job_id: str) -> JobRecord:
        now = now_iso()
        with self._transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                raise JobNotFoundError(job_id)
            current = JobStatus(row["status"])
            require_transition(current, JobStatus.QUEUED)
            if int(row["attempt_count"]) >= int(row["max_attempts"]):
                raise JobStateError(
                    f"Job {job_id} reached max_attempts={row['max_attempts']}"
                )
            connection.execute(
                """
                UPDATE jobs SET status=?,stage=?,worker_id=NULL,updated_at=?,started_at=NULL,
                    finished_at=NULL,heartbeat_at=NULL,result_path=NULL,error_code=NULL,error_message=NULL
                WHERE job_id=?
                """,
                (JobStatus.QUEUED.value, JobStage.SUBMITTED.value, now, job_id),
            )
            self._append_event_tx(connection, job_id, "JOB_REQUEUED",
                                  JobStatus.QUEUED, JobStage.SUBMITTED,
                                  f"next_attempt={int(row['attempt_count']) + 1}")
            return JobRecord.from_row(connection.execute(
                "SELECT * FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone())

    def schema_export(self) -> dict[str, Any]:
        with self._read_connection() as connection:
            objects = connection.execute(
                """
                SELECT type,name,sql FROM sqlite_master
                WHERE type IN ('table','index') AND name NOT LIKE 'sqlite_%'
                ORDER BY type,name
                """
            ).fetchall()
            version = connection.execute("SELECT MAX(version) AS version FROM schema_version").fetchone()["version"]
            pragmas = {
                "journal_mode": connection.execute("PRAGMA journal_mode").fetchone()[0],
                "foreign_keys": connection.execute("PRAGMA foreign_keys").fetchone()[0],
                "busy_timeout": connection.execute("PRAGMA busy_timeout").fetchone()[0],
            }
        return {
            "schema_version": int(version),
            "pragmas": pragmas,
            "objects": [dict(row) for row in objects],
        }

    def health_check(self) -> bool:
        """Verify that the configured SQLite store is reachable and initialized."""
        with self._read_connection() as connection:
            probe = connection.execute("SELECT 1 AS ok").fetchone()["ok"]
            version = connection.execute(
                "SELECT MAX(version) AS version FROM schema_version"
            ).fetchone()["version"]
        return probe == 1 and int(version) == SCHEMA_VERSION
