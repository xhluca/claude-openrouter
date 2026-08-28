#!/usr/bin/env python3
"""Remove private paths, hidden-thought flashes, and hostile cast whitespace."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DEMO_ROOT = re.compile(r"/tmp/claude-openrouter-demo\.[A-Za-z0-9]+")
TRANSIENT_THOUGHT_MARKERS = (
    "I've verified. Now let me answer briefly.",
    "The user is asking what model powers me",
    "The user asked two things: what model powers me",
)
TRANSIENT_BURST_WINDOW = 0.005


def remove_transient_thought_bursts(values: list[object]) -> tuple[list[object], int]:
    burst_times = [
        float(value[0])
        for value in values
        if isinstance(value, list)
        and len(value) == 3
        and value[1] == "o"
        and isinstance(value[0], int | float)
        and isinstance(value[2], str)
        and any(marker in value[2] for marker in TRANSIENT_THOUGHT_MARKERS)
    ]
    if not burst_times:
        return values, 0

    cleaned: list[object] = []
    removed = 0
    for value in values:
        if (
            isinstance(value, list)
            and len(value) == 3
            and value[1] == "o"
            and isinstance(value[0], int | float)
            and any(
                abs(float(value[0]) - burst_time) <= TRANSIENT_BURST_WINDOW
                for burst_time in burst_times
            )
        ):
            removed += 1
            continue
        cleaned.append(value)
    return cleaned, removed


def main() -> int:
    if len(sys.argv) not in {2, 4} or (len(sys.argv) == 4 and sys.argv[2] != "--secret-file"):
        raise SystemExit("usage: sanitize-demo-cast.py CAST [--secret-file FILE]")
    path = Path(sys.argv[1])
    secret = Path(sys.argv[3]).read_text(encoding="utf-8").strip() if len(sys.argv) == 4 else ""
    values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    values, removed = remove_transient_thought_bursts(values)
    output: list[str] = []
    for value in values:
        if isinstance(value, list) and len(value) == 3 and isinstance(value[2], str):
            if secret and secret in value[2]:
                raise SystemExit("refusing to publish a cast containing the demo credential")
            value[2] = DEMO_ROOT.sub("~", value[2]).replace("\u00a0", " ")
        output.append(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    path.write_text("\n".join(output) + "\n", encoding="utf-8")
    if removed:
        print(f"Removed {removed} transient hidden-thought events from the cast.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
