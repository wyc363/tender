from __future__ import annotations

import argparse
from pathlib import Path

from ..config import load_config
from .app import create_app


DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "pipeline.local.yaml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local FastAPI job service")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    import uvicorn

    uvicorn.run(
        create_app(config),
        host=config.http_host,
        port=config.http_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
