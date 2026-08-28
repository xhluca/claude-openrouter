#!/usr/bin/env python3
"""Reject a demo cast containing any mounted credential value."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def credential_values(value: Any, key: str = "") -> list[str]:
    if isinstance(value, dict):
        return [secret for name, item in value.items() for secret in credential_values(item, name)]
    if isinstance(value, list):
        return [secret for item in value for secret in credential_values(item, key)]
    if isinstance(value, str) and any(marker in key.casefold() for marker in ("token", "secret")):
        return [value] if len(value) >= 12 else []
    return []


def secrets_from(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8").strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return [text] if len(text) >= 12 else []
    return credential_values(value)


def main() -> int:
    if len(sys.argv) < 3:
        raise SystemExit("usage: verify-demo-secrets.py CAST CREDENTIAL_FILE...")
    cast = Path(sys.argv[1]).read_text(encoding="utf-8")
    for filename in sys.argv[2:]:
        for secret in secrets_from(Path(filename)):
            if secret in cast:
                raise SystemExit(f"demo cast contains a credential from {filename}")
    print(f"Verified that {len(sys.argv) - 2} credential files did not leak into the cast.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
