"""Private Anthropic API credential storage for optional API-billed routing."""

from __future__ import annotations

import re

from .paths import anthropic_credential_path
from .storage import atomic_write_text

KEY_PATTERN = re.compile(r"^sk-ant-[^\s]{10,}$")


def validate_anthropic_key_shape(key: str) -> None:
    if not KEY_PATTERN.fullmatch(key):
        raise ValueError("the Anthropic key has an unexpected format (expected sk-ant-...)")


def read_anthropic_credential() -> str:
    path = anthropic_credential_path()
    try:
        key = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise RuntimeError(f"Anthropic API credential not found at {path}") from exc
    validate_anthropic_key_shape(key)
    return key


def write_anthropic_credential(key: str) -> None:
    validate_anthropic_key_shape(key)
    atomic_write_text(anthropic_credential_path(), f"{key}\n", 0o600)
