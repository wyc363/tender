from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .client_api_adapter import ClientApiAdapterError, build_client_api_outputs
from .config import load_config


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Convert an existing Judge result to the client API response format")
    parser.add_argument("--document-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=project / "config" / "pipeline.local.yaml")
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--chat-id")
    parser.add_argument("--judge-env-file", type=Path)
    parser.add_argument("--enrich-fail", action="store_true")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        judge_env_file = args.judge_env_file or config.judge_env_file
        content, response = build_client_api_outputs(
            args.document_root,
            catalog_path=args.catalog,
            chat_id=args.chat_id,
            judge_env_file=judge_env_file,
            enrich_fail=args.enrich_fail,
            pass_evidence_top_n=config.client_pass_evidence_top_n,
            model_environment=None if args.judge_env_file else config.model_environment("judge"),
            enrichment_settings=config.client_settings,
        )
    except (ClientApiAdapterError, FileNotFoundError, KeyError, ValueError) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps({
        "status": "PASS",
        "client_content_result": str(content),
        "client_api_response": str(response),
        "api_calls": "0 unless --enrich-fail is used when the model judgment is FAIL",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
