"""Small, atomic, permission-conscious persistence helpers."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def ensure_private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def atomic_write_text(path: Path, content: str, mode: int = 0o600) -> None:
    ensure_private_dir(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, mode)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def atomic_write_json(path: Path, value: Any, mode: int = 0o600) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n", mode)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON in {path}: {exc}") from exc


def read_json_object(path: Path, *, missing_ok: bool = False) -> dict[str, Any]:
    try:
        value = read_json(path)
    except FileNotFoundError:
        if missing_ok:
            return {}
        raise
    if not isinstance(value, dict):
        raise RuntimeError(f"expected a JSON object in {path}")
    return value

