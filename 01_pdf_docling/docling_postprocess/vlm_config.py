"""Independent Parser VLM configuration for Phase A2."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path


PARSER_INPUT_PRICE_CNY_PER_MILLION = 0.2
PARSER_OUTPUT_PRICE_CNY_PER_MILLION = 0.8
API_REGION = "beijing"


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _load_env_file(path: Path) -> None:
    """Load a small .env file without adding a dependency; existing env wins."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value and value[:1] == value[-1:] and value[:1] in {'"', "'"}:
            value = value[1:-1]
        if key.startswith("PARSER_VLM_"):
            os.environ.setdefault(key, value)


@dataclass(frozen=True)
class ParserVLMSettings:
    api_base: str
    api_key: str
    model: str
    temperature: float
    max_tokens: int
    enable_thinking: bool
    timeout_seconds: float
    max_retries: int
    max_pixels: int
    concurrency: int
    api_region: str = API_REGION
    input_price_cny_per_million: float = PARSER_INPUT_PRICE_CNY_PER_MILLION
    output_price_cny_per_million: float = PARSER_OUTPUT_PRICE_CNY_PER_MILLION

    @property
    def api_configured(self) -> bool:
        return bool(self.api_base and self.api_key and self.model and
                    "{WorkspaceId}" not in self.api_base)

    def public_dict(self) -> dict:
        values = asdict(self)
        values["api_key"] = "CONFIGURED" if self.api_key else "MISSING"
        return values


def load_parser_vlm_settings(env_file: Path | None = None) -> ParserVLMSettings:
    local_env = env_file or Path(__file__).resolve().parents[1] / ".env"
    _load_env_file(local_env.resolve())
    return ParserVLMSettings(
        api_base=os.getenv("PARSER_VLM_API_BASE", "").strip(),
        api_key=os.getenv("PARSER_VLM_API_KEY", "").strip(),
        model=os.getenv("PARSER_VLM_MODEL", "qwen3.7-flash").strip(),
        temperature=float(os.getenv("PARSER_VLM_TEMPERATURE", "0")),
        max_tokens=int(os.getenv("PARSER_VLM_MAX_TOKENS", "2500")),
        enable_thinking=_as_bool(os.getenv("PARSER_VLM_ENABLE_THINKING", "false")),
        timeout_seconds=float(os.getenv("PARSER_VLM_TIMEOUT_SECONDS", "120")),
        max_retries=int(os.getenv("PARSER_VLM_MAX_RETRIES", "1")),
        max_pixels=int(os.getenv("PARSER_VLM_MAX_PIXELS", "1572864")),
        concurrency=int(os.getenv("PARSER_VLM_CONCURRENCY", "1")),
    )


def validate_parser_vlm_settings(settings: ParserVLMSettings, require_api: bool) -> None:
    if require_api and not settings.api_configured:
        raise RuntimeError(
            "--run requires independent PARSER_VLM_API_BASE, PARSER_VLM_API_KEY and "
            "PARSER_VLM_MODEL. The endpoint must contain a real Beijing WorkspaceId."
        )
    if settings.temperature != 0:
        raise ValueError("Phase A2 requires PARSER_VLM_TEMPERATURE=0")
    if settings.concurrency != 1:
        raise ValueError("Phase A2 fixed-page POC requires PARSER_VLM_CONCURRENCY=1")
    if settings.max_tokens < 1 or settings.timeout_seconds <= 0:
        raise ValueError("Parser VLM token and timeout settings must be positive")
    if settings.max_retries < 0 or settings.max_pixels < 1:
        raise ValueError("Parser VLM retries must be non-negative and max_pixels positive")


def estimate_cost(settings: ParserVLMSettings, prompt_tokens: int | None,
                  completion_tokens: int | None) -> dict:
    if prompt_tokens is None or completion_tokens is None:
        return {"status": "unavailable", "currency": "CNY", "estimated_cost": None,
                "reason": "usage tokens unavailable"}
    input_cost = prompt_tokens / 1_000_000 * settings.input_price_cny_per_million
    output_cost = completion_tokens / 1_000_000 * settings.output_price_cny_per_million
    return {"status": "estimate", "currency": "CNY",
            "input_cost": input_cost, "output_cost": output_cost,
            "estimated_cost": input_cost + output_cost,
            "input_price_per_million": settings.input_price_cny_per_million,
            "output_price_per_million": settings.output_price_cny_per_million}
