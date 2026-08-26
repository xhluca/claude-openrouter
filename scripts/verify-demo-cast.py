#!/usr/bin/env python3
"""Verify required text across the complete asciicast output stream."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        raise SystemExit("usage: verify-demo-cast.py CAST REQUIRED_TEXT...")
    path = Path(sys.argv[1])
    stream: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        event = json.loads(line)
        if (
            isinstance(event, list)
            and len(event) == 3
            and event[1] == "o"
            and isinstance(event[2], str)
        ):
            stream.append(event[2])
    output = "".join(stream)
    missing = [required for required in sys.argv[2:] if required not in output]
    if missing:
        raise SystemExit(f"demo cast is missing: {', '.join(missing)}")
    print(f"Verified {len(sys.argv) - 2} required demo interactions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
