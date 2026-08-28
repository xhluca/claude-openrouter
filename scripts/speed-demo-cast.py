#!/usr/bin/env python3
"""Compress Claude's active response interval without speeding up user actions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ACTIVE_MARKER = "esc to interrupt"
IDLE_MARKER = "? for shortcuts"


def event_output(event: Any) -> str:
    if (
        isinstance(event, list)
        and len(event) == 3
        and event[1] == "o"
        and isinstance(event[2], str)
    ):
        return event[2]
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cast", type=Path)
    parser.add_argument("--factor", type=float, default=4.0)
    args = parser.parse_args()
    if args.factor <= 1:
        raise SystemExit("--factor must be greater than 1")

    lines = args.cast.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise SystemExit(f"empty asciicast: {args.cast}")
    header = lines[0]
    events = [json.loads(line) for line in lines[1:]]

    active_at: float | None = None
    idle_at: float | None = None
    for event in events:
        output = event_output(event)
        if active_at is None and ACTIVE_MARKER in output:
            active_at = float(event[0])
            continue
        if active_at is not None and IDLE_MARKER in output:
            idle_at = float(event[0])
            break
    if active_at is None or idle_at is None or idle_at <= active_at:
        raise SystemExit("could not locate Claude's active response interval in the cast")

    saved = (idle_at - active_at) * (1 - 1 / args.factor)
    for event in events:
        timestamp = float(event[0])
        if timestamp <= active_at:
            continue
        if timestamp <= idle_at:
            event[0] = round(active_at + (timestamp - active_at) / args.factor, 6)
        else:
            event[0] = round(timestamp - saved, 6)

    rendered = [header, *(json.dumps(event, separators=(",", ":")) for event in events)]
    args.cast.write_text("\n".join(rendered) + "\n", encoding="utf-8")
    print(
        f"Compressed Claude response from {idle_at - active_at:.1f}s to "
        f"{(idle_at - active_at) / args.factor:.1f}s ({args.factor:g}x)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
