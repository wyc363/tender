from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parents[2]
EXPERIMENT_DIR = PROJECT_DIR.parent
DEFAULT_OUTPUT_DIR = EXPERIMENT_DIR / "outputs" / "v03_pilot5"
EXPECTED_MODEL_NAME = "Qwen/Qwen3-8B"


@dataclass(frozen=True)
class V03Settings:
    project_dir: Path
    output_dir: Path
    train_path: Path
    validation_path: Path
    test_path: Path
    optimizer_train_path: Path
    optimizer_validation_path: Path
    dataset_snapshot_path: Path
    legacy_prompt_path: Path
    manual_prompt_path: Path
    optimized_dir: Path
    compiled_program_path: Path
    results_dir: Path
    frozen_config_dir: Path
    api_base: str | None
    api_key: str | None
    model_name: str | None
    temperature: float
    max_tokens: int
    timeout_seconds: float
    max_retries: int
    retry_backoff_seconds: float
    force_stream: bool
    enable_thinking: bool
    mipro_auto: str | None
    mipro_num_trials: int | None
    mipro_num_candidates: int | None
    mipro_seed: int
    mipro_num_threads: int
    mipro_max_errors: int

    @property
    def api_configured(self) -> bool:
        return bool(self.api_base and self.api_key and self.model_name)

    def public_dict(self) -> dict:
        value = asdict(self)
        value["api_key"] = "CONFIGURED" if self.api_key else "MISSING"
        return {key: str(item) if isinstance(item, Path) else item for key, item in value.items()}


def _optional_int(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    return int(value) if value else None


def load_v03_settings(output_dir: str | Path | None = None) -> V03Settings:
    load_dotenv(PROJECT_DIR / ".env")
    output = Path(output_dir).expanduser().resolve() if output_dir else DEFAULT_OUTPUT_DIR.resolve()
    prompt_dir = Path(__file__).resolve().parent / "prompts"
    auto_value = os.getenv("V03_MIPRO_AUTO", "light").strip().lower()
    mipro_auto = None if auto_value in {"", "none", "null"} else auto_value
    mipro_num_trials = _optional_int("V03_MIPRO_NUM_TRIALS")
    if mipro_auto and mipro_num_trials is not None:
        raise ValueError("Set either V03_MIPRO_AUTO or V03_MIPRO_NUM_TRIALS, not both")
    if mipro_auto not in {None, "light", "medium", "heavy"}:
        raise ValueError("V03_MIPRO_AUTO must be light, medium, heavy, or none")
    return V03Settings(
        project_dir=PROJECT_DIR.resolve(), output_dir=output,
        train_path=output / "data/pilot5_train.jsonl",
        validation_path=output / "data/pilot5_validation.jsonl",
        test_path=output / "data/pilot5_test.jsonl",
        optimizer_train_path=output / "data/pilot5_optimizer_train.jsonl",
        optimizer_validation_path=output / "data/pilot5_optimizer_validation.jsonl",
        dataset_snapshot_path=output / "artifacts/frozen_dataset/dataset_snapshot.json",
        legacy_prompt_path=prompt_dir / "legacy_system_prompt.txt",
        manual_prompt_path=prompt_dir / "manual_decision_prompt.txt",
        optimized_dir=output / "artifacts/dspy_optimized",
        compiled_program_path=output / "artifacts/dspy_optimized/compiled_program.json",
        results_dir=output / "results",
        frozen_config_dir=output / "artifacts/frozen_config",
        api_base=os.getenv("MODEL_API_BASE"), api_key=os.getenv("MODEL_API_KEY"),
        model_name=os.getenv("MODEL_NAME"),
        temperature=float(os.getenv("MODEL_TEMPERATURE", "0")),
        max_tokens=int(os.getenv("MODEL_MAX_TOKENS", "1024")),
        timeout_seconds=float(os.getenv("MODEL_TIMEOUT_SECONDS", "120")),
        max_retries=int(os.getenv("MODEL_MAX_RETRIES", "2")),
        retry_backoff_seconds=float(os.getenv("MODEL_RETRY_BACKOFF_SECONDS", "2")),
        force_stream=os.getenv("MODEL_FORCE_STREAM", "auto").strip().lower() in {"1", "true", "yes", "on"},
        enable_thinking=os.getenv("MODEL_ENABLE_THINKING", "false").strip().lower() in {"1", "true", "yes", "on"},
        mipro_auto=mipro_auto, mipro_num_trials=mipro_num_trials,
        mipro_num_candidates=_optional_int("V03_MIPRO_NUM_CANDIDATES"),
        mipro_seed=int(os.getenv("V03_MIPRO_SEED", "42")),
        mipro_num_threads=int(os.getenv("V03_MIPRO_NUM_THREADS", "1")),
        mipro_max_errors=int(os.getenv("V03_MIPRO_MAX_ERRORS", "20")),
    )


def validate_experiment_settings(settings: V03Settings, require_api: bool = True) -> None:
    if require_api and not settings.api_configured:
        raise RuntimeError("MODEL_API_BASE, MODEL_API_KEY and MODEL_NAME are required")
    if settings.temperature != 0:
        raise RuntimeError("V0.3 controlled experiment requires MODEL_TEMPERATURE=0")
    for path in (settings.dataset_snapshot_path, settings.train_path, settings.validation_path, settings.test_path):
        if not path.exists():
            raise FileNotFoundError(path)
