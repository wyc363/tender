from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SECRET_RE = re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)([^\s,;]+)")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest().upper()


def sha256_value(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest().upper()


def tree_fingerprint(paths: list[Path]) -> str:
    rows: list[tuple[str, str]] = []
    for path in paths:
        if path.is_file():
            rows.append((path.name, sha256_file(path)))
        elif path.is_dir():
            for item in sorted(path.rglob("*.py")):
                if "__pycache__" not in item.parts:
                    rows.append((str(item.relative_to(path)), sha256_file(item)))
    return sha256_value(rows)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def redact(text: str) -> str:
    return SECRET_RE.sub(r"\1***", text)


def env_has(path: Path, names: list[str]) -> dict[str, bool]:
    values: dict[str, str] = {}
    if path.is_file():
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return {name: bool(os.getenv(name) or values.get(name)) for name in names}

