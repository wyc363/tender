from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_config
from .job_models import JobStateError, JobStatus
from .job_service import JobService
from .job_store import JobNotFoundError, JobStore


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "pipeline.local.yaml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Persistent tender review job CLI")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    subparsers = parser.add_subparsers(dest="command", required=True)

    submit = subparsers.add_parser("submit", help="Create a local-file job without running the pipeline")
    submit.add_argument("--pdf", type=Path, required=True)
    submit.add_argument("--chat-id")
    submit.add_argument("--document-id")
    submit.add_argument("--rule-id")

    status = subparsers.add_parser("status", help="Read persisted job metadata")
    status.add_argument("job_id")

    result = subparsers.add_parser("result", help="Show the completed client result path")
    result.add_argument("job_id")

    requeue = subparsers.add_parser("requeue", help="Explicitly requeue FAILED or INTERRUPTED job")
    requeue.add_argument("job_id")

    events = subparsers.add_parser("events", help="List the persisted event history")
    events.add_argument("job_id")
    return parser


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    config = load_config(args.config)
    store = JobStore(config.effective_job_database_path, busy_timeout_ms=config.job_busy_timeout_ms)
    try:
        if args.command == "submit":
            submission = JobService(config, store).submit_local_file(
                args.pdf,
                chat_id=args.chat_id,
                document_id=args.document_id,
                rule_id=args.rule_id,
            )
            _print({"created": submission.created, "job": submission.job.to_public_dict(), "api_calls": 0})
        elif args.command == "status":
            _print(store.get_job(args.job_id).to_public_dict())
        elif args.command == "result":
            job = store.get_job(args.job_id)
            _print({
                "job_id": job.job_id,
                "status": job.status.value,
                "result_path": job.result_path if job.status is JobStatus.COMPLETED else None,
            })
        elif args.command == "requeue":
            _print(store.requeue_job(args.job_id).to_public_dict())
        elif args.command == "events":
            _print([event.to_public_dict() for event in store.list_events(args.job_id)])
    except (FileNotFoundError, ValueError, JobNotFoundError, JobStateError) as exc:
        _print({"status": "ERROR", "error": str(exc)})
        raise SystemExit(1)


if __name__ == "__main__":
    main()
