from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


@dataclass(frozen=True)
class PipelineConfig:
    config_path: Path
    project_root: Path
    output_root: Path
    rule_store_path: Path
    retrieval_config_path: Path
    prompt_program_path: Path
    docling_project: Path
    parser_project: Path
    chunk_project: Path
    retrieval_project: Path
    judge_project: Path
    docling_python: Path
    parser_python: Path
    chunk_python: Path
    retrieval_python: Path
    judge_python: Path
    parser_env_file: Path
    judge_env_file: Path
    temp_root: Path | None = None
    staging_root: Path | None = None
    parser_settings: dict[str, Any] = field(default_factory=dict)
    judge_settings: dict[str, Any] = field(default_factory=dict)
    client_settings: dict[str, Any] = field(default_factory=dict)
    enabled_review_points: tuple[str, ...] = ("RP_REGION_INDUSTRY_AWARD",)
    job_database_path: Path | None = None
    job_settings: dict[str, Any] = field(default_factory=dict)
    http_settings: dict[str, Any] = field(default_factory=dict)
    file_url_settings: dict[str, Any] = field(default_factory=dict)
    client_sync_settings: dict[str, Any] = field(default_factory=dict)

    @property
    def client_pass_evidence_top_n(self) -> int:
        return int(self.client_settings.get("pass_evidence_top_n", 3))

    @property
    def effective_job_database_path(self) -> Path:
        return (self.job_database_path or self.output_root / ".state" / "jobs.sqlite3").resolve()

    @property
    def effective_staging_root(self) -> Path:
        return (self.staging_root or self.output_root / ".staging").resolve()

    @property
    def job_poll_interval_seconds(self) -> float:
        return float(self.job_settings.get("poll_interval_seconds", 2))

    @property
    def job_heartbeat_interval_seconds(self) -> float:
        return float(self.job_settings.get("heartbeat_interval_seconds", 10))

    @property
    def job_stale_after_seconds(self) -> float:
        return float(self.job_settings.get("stale_after_seconds", 120))

    @property
    def job_max_attempts(self) -> int:
        return int(self.job_settings.get("max_attempts", 2))

    @property
    def job_busy_timeout_ms(self) -> int:
        return int(self.job_settings.get("busy_timeout_ms", 5000))

    @property
    def http_host(self) -> str:
        return str(self.http_settings.get("host", "127.0.0.1"))

    @property
    def http_port(self) -> int:
        return int(self.http_settings.get("port", 8000))

    @property
    def http_api_prefix(self) -> str:
        value = str(self.http_settings.get("api_prefix", "/api/v1")).rstrip("/")
        return value or ""

    @property
    def http_docs_enabled(self) -> bool:
        return bool(self.http_settings.get("docs_enabled", True))

    @property
    def client_sync_enabled(self) -> bool:
        return bool(self.client_sync_settings.get("enabled", True))

    @property
    def client_sync_route_path(self) -> str:
        return str(self.client_sync_settings.get("route_path", "/api/v1/review"))

    @property
    def client_sync_wait_timeout_seconds(self) -> float:
        return float(self.client_sync_settings.get("wait_timeout_seconds", 1800))

    @property
    def client_sync_poll_interval_seconds(self) -> float:
        return float(self.client_sync_settings.get("poll_interval_seconds", 2))

    @property
    def file_url_enabled(self) -> bool:
        return bool(self.file_url_settings.get("enabled", True))

    @property
    def file_url_allowed_schemes(self) -> tuple[str, ...]:
        return tuple(str(value).lower() for value in
                     self.file_url_settings.get("allowed_schemes", ["http", "https"]))

    @property
    def file_url_allow_private_network(self) -> bool:
        return bool(self.file_url_settings.get("allow_private_network", False))

    @property
    def file_url_trusted_private_hosts(self) -> tuple[str, ...]:
        return tuple(str(value) for value in
                     self.file_url_settings.get("trusted_private_hosts", []))

    @property
    def file_url_trusted_private_cidrs(self) -> tuple[str, ...]:
        return tuple(str(value) for value in
                     self.file_url_settings.get("trusted_private_cidrs", []))

    @property
    def file_url_max_redirects(self) -> int:
        return int(self.file_url_settings.get("max_redirects", 3))

    @property
    def file_url_connect_timeout_seconds(self) -> float:
        return float(self.file_url_settings.get("connect_timeout_seconds", 10))

    @property
    def file_url_read_timeout_seconds(self) -> float:
        return float(self.file_url_settings.get("read_timeout_seconds", 60))

    @property
    def file_url_write_timeout_seconds(self) -> float:
        return float(self.file_url_settings.get("write_timeout_seconds", 10))

    @property
    def file_url_pool_timeout_seconds(self) -> float:
        return float(self.file_url_settings.get("pool_timeout_seconds", 10))

    @property
    def file_url_max_download_bytes(self) -> int:
        return int(self.file_url_settings.get("max_download_bytes", 64 * 1024 * 1024))

    @property
    def file_url_chunk_size_bytes(self) -> int:
        return int(self.file_url_settings.get("chunk_size_bytes", 64 * 1024))

    def model_environment(self, stage: str) -> dict[str, str]:
        if stage == "parser":
            return _model_environment(self.parser_settings, self.parser_env_file, parser=True)
        if stage == "judge":
            return _model_environment(self.judge_settings, self.judge_env_file, parser=False)
        raise ValueError(f"Unsupported model stage: {stage}")

    def public_model_settings(self, stage: str) -> dict[str, Any]:
        values = self.model_environment(stage)
        prefix = "PARSER_VLM_" if stage == "parser" else "MODEL_"
        return {
            key: ("CONFIGURED" if key.endswith("API_KEY") and value else "MISSING"
                  if key.endswith("API_KEY") else value)
            for key, value in values.items()
            if key.startswith(prefix)
        }


def _expand_env(value: Any, file_values: dict[str, str] | None = None) -> Any:
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name, fallback = match.group(1), match.group(2)
        current = os.getenv(name)
        if current in (None, "") and file_values:
            current = file_values.get(name)
        if current not in (None, ""):
            return current
        if fallback is not None:
            return fallback
        raise ValueError(f"Environment variable {name} is required by pipeline config")

    return _ENV_PATTERN.sub(replace, value)


def _path(value: str | Path, base: Path, file_values: dict[str, str] | None = None) -> Path:
    p = Path(str(_expand_env(value, file_values))).expanduser()
    return (p if p.is_absolute() else base / p).resolve()


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _stringify(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _model_environment(settings: dict[str, Any], env_file: Path, *, parser: bool) -> dict[str, str]:
    file_values = _read_env_file(env_file)
    prefix = "PARSER_VLM_" if parser else "MODEL_"
    bindings = {
        "api_base": prefix + "API_BASE",
        "api_key": prefix + "API_KEY",
        "model": prefix + ("MODEL" if parser else "NAME"),
        "temperature": prefix + "TEMPERATURE",
        "max_tokens": prefix + "MAX_TOKENS",
        "enable_thinking": prefix + "ENABLE_THINKING",
        "timeout_seconds": prefix + "TIMEOUT_SECONDS",
        "retry_count": prefix + "MAX_RETRIES",
    }
    if parser:
        bindings.update({
            "max_pixels": "PARSER_VLM_MAX_PIXELS",
            "concurrency": "PARSER_VLM_CONCURRENCY",
        })
    else:
        bindings.update({
            "retry_backoff_seconds": "MODEL_RETRY_BACKOFF_SECONDS",
            "force_stream": "MODEL_FORCE_STREAM",
        })
    result: dict[str, str] = {}
    for setting_name, standard_name in bindings.items():
        custom_name = str(settings.get(f"{setting_name}_env") or standard_name)
        value = os.getenv(custom_name)
        if value in (None, ""):
            value = file_values.get(custom_name) or file_values.get(standard_name)
        if value in (None, "") and settings.get(setting_name) is not None:
            value = _stringify(settings[setting_name])
        if value not in (None, ""):
            result[standard_name] = str(value)
    return result


def _runtime_python(runtime: dict[str, Any], name: str, project_root: Path,
                    file_values: dict[str, str] | None = None) -> Path:
    env_name = str(runtime.get(f"{name}_env") or "")
    value = os.getenv(env_name) if env_name else None
    if not value and env_name and file_values:
        value = file_values.get(env_name)
    if not value:
        value = runtime.get(name)
    if not value:
        value = runtime.get("python")
    if not value:
        return Path(sys.executable).resolve()
    return _path(str(value), project_root, file_values)


def load_config(path: str | Path) -> PipelineConfig:
    source = Path(path).expanduser().resolve()
    data: dict[str, Any] = yaml.safe_load(source.read_text(encoding="utf-8-sig")) or {}
    base = source.parent
    pipeline_env = _read_env_file(base.parent / ".env")
    project = data.get("project") or {}
    paths = data.get("paths") or {}
    projects = data.get("projects") or {}
    runtime = data.get("runtime") or {}
    env_files = data.get("env_files") or {}
    job = data.get("job") or {}
    http = data.get("http") or {}
    file_url = data.get("file_url") or {}
    client_sync = data.get("client_sync") or {}
    project_root_value = project.get("root", data.get("project_root"))
    if project_root_value is None:
        raise ValueError("Missing pipeline config: project.root")
    project_root = _path(project_root_value, base, pipeline_env)

    def value(section: dict[str, Any], nested: str, legacy: str, default: Any = None) -> Any:
        selected = section.get(nested, data.get(legacy, default))
        if selected is None:
            raise ValueError(f"Missing pipeline config: {legacy}")
        return selected

    def p(section: dict[str, Any], nested: str, legacy: str, default: Any = None) -> Path:
        return _path(value(section, nested, legacy, default), project_root, pipeline_env)

    output_root = p(paths, "output_root", "output_root")
    temp_value = paths.get("temp_root")
    staging_value = paths.get("staging_root")
    job_database_env = str(job.get("database_path_env") or "TENDER_REVIEW_JOB_DB")
    job_database_value = os.getenv(job_database_env) or pipeline_env.get(job_database_env) or job.get("database_path")
    job_database_path = (_path(job_database_value, project_root, pipeline_env)
                         if job_database_value else (output_root / ".state" / "jobs.sqlite3").resolve())
    config = PipelineConfig(
        config_path=source,
        project_root=project_root,
        output_root=output_root,
        temp_root=_path(temp_value, project_root, pipeline_env) if temp_value else output_root / ".tmp",
        staging_root=_path(staging_value, project_root, pipeline_env) if staging_value else output_root / ".staging",
        rule_store_path=p(paths, "rule_store", "rule_store_path"),
        retrieval_config_path=p(paths, "retrieval_config", "retrieval_config_path"),
        prompt_program_path=p(paths, "prompt_program", "prompt_program_path"),
        docling_project=p(projects, "docling", "docling_project"),
        parser_project=p(projects, "parser", "parser_project"),
        chunk_project=p(projects, "chunk", "chunk_project"),
        retrieval_project=p(projects, "retrieval", "retrieval_project"),
        judge_project=p(projects, "judge", "judge_project"),
        docling_python=_runtime_python(runtime, "docling_python", project_root, pipeline_env)
        if runtime else p(data, "docling_python", "docling_python"),
        parser_python=_runtime_python(runtime, "parser_python", project_root, pipeline_env)
        if runtime else p(data, "parser_python", "parser_python"),
        chunk_python=_runtime_python(runtime, "chunk_python", project_root, pipeline_env)
        if runtime else p(data, "chunk_python", "chunk_python"),
        retrieval_python=_runtime_python(runtime, "retrieval_python", project_root, pipeline_env)
        if runtime else p(data, "retrieval_python", "retrieval_python"),
        judge_python=_runtime_python(runtime, "judge_python", project_root, pipeline_env)
        if runtime else p(data, "judge_python", "judge_python"),
        parser_env_file=p(env_files, "parser", "parser_env_file"),
        judge_env_file=p(env_files, "judge", "judge_env_file"),
        parser_settings=dict(data.get("parser") or {}),
        judge_settings=dict(data.get("judge") or {}),
        client_settings=dict(data.get("client") or {}),
        enabled_review_points=tuple(
            (data.get("review") or {}).get("enabled_review_points")
            or ["RP_REGION_INDUSTRY_AWARD"]
        ),
        job_database_path=job_database_path,
        job_settings={key: value for key, value in job.items()
                      if key not in {"database_path", "database_path_env"}},
        http_settings=dict(http),
        file_url_settings=dict(file_url),
        client_sync_settings=dict(client_sync),
    )
    if config.client_pass_evidence_top_n < 1:
        raise ValueError("client.pass_evidence_top_n must be positive")
    if not config.enabled_review_points:
        raise ValueError("review.enabled_review_points cannot be empty")
    if config.job_poll_interval_seconds <= 0:
        raise ValueError("job.poll_interval_seconds must be positive")
    if config.job_heartbeat_interval_seconds <= 0:
        raise ValueError("job.heartbeat_interval_seconds must be positive")
    if config.job_stale_after_seconds <= 0:
        raise ValueError("job.stale_after_seconds must be positive")
    if config.job_max_attempts < 1:
        raise ValueError("job.max_attempts must be positive")
    if int(config.job_settings.get("worker_concurrency", 1)) != 1:
        raise ValueError("job.worker_concurrency must be 1 in Phase 3")
    if not config.http_host.strip():
        raise ValueError("http.host cannot be empty")
    if not 1 <= config.http_port <= 65535:
        raise ValueError("http.port must be between 1 and 65535")
    if config.http_api_prefix and not config.http_api_prefix.startswith("/"):
        raise ValueError("http.api_prefix must start with '/'")
    if not config.client_sync_route_path.startswith("/"):
        raise ValueError("client_sync.route_path must start with '/'")
    if config.client_sync_wait_timeout_seconds <= 0:
        raise ValueError("client_sync.wait_timeout_seconds must be positive")
    if config.client_sync_poll_interval_seconds <= 0:
        raise ValueError("client_sync.poll_interval_seconds must be positive")
    if not config.file_url_allowed_schemes or not set(config.file_url_allowed_schemes) <= {"http", "https"}:
        raise ValueError("file_url.allowed_schemes must contain only http and/or https")
    if config.file_url_max_redirects < 0:
        raise ValueError("file_url.max_redirects cannot be negative")
    if config.file_url_max_download_bytes <= 0:
        raise ValueError("file_url.max_download_bytes must be positive")
    if config.file_url_chunk_size_bytes <= 0:
        raise ValueError("file_url.chunk_size_bytes must be positive")
    if any(value <= 0 for value in (
        config.file_url_connect_timeout_seconds,
        config.file_url_read_timeout_seconds,
        config.file_url_write_timeout_seconds,
        config.file_url_pool_timeout_seconds,
    )):
        raise ValueError("file_url timeout values must be positive")
    return config
