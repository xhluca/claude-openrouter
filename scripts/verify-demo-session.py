#!/usr/bin/env python3
"""Verify that Claude Code saved the expected live assistant response."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def contains_text(value: Any, expected: str) -> bool:
    if isinstance(value, str):
        return expected in value
    if isinstance(value, list):
        return any(contains_text(item, expected) for item in value)
    if isinstance(value, dict):
        return any(contains_text(item, expected) for item in value.values())
    return False


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: verify-demo-session.py CLAUDE_CONFIG_DIR EXPECTED_TEXT")
    config_dir = Path(sys.argv[1])
    expected = sys.argv[2]
    for transcript in config_dir.rglob("*.jsonl"):
        for line in transcript.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = event.get("message") if isinstance(event, dict) else None
            if (
                isinstance(message, dict)
                and message.get("role") == "assistant"
                and contains_text(message.get("content"), expected)
            ):
                print(f"Verified live assistant response in {transcript}.")
                return 0
    raise SystemExit(f"no assistant response contained: {expected}")


if __name__ == "__main__":
    raise SystemExit(main())
