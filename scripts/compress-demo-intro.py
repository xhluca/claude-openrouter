#!/usr/bin/env python3
"""Compress an asciicast prefix while preserving all later playback intervals."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def retime_event(event: Any, through: float, duration: float) -> None:
    if not isinstance(event, list) or not event or not isinstance(event[0], int | float):
        raise SystemExit(f"invalid asciicast event: {event!r}")
    timestamp = float(event[0])
    if timestamp <= through:
        event[0] = round(timestamp * duration / through, 6)
    else:
        event[0] = round(timestamp - (through - duration), 6)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cast", type=Path)
    parser.add_argument("--through", type=float, required=True)
    parser.add_argument("--duration", type=float, required=True)
    args = parser.parse_args()
    if args.through <= 0:
        raise SystemExit("--through must be greater than zero")
    if args.duration <= 0 or args.duration >= args.through:
        raise SystemExit("--duration must be greater than zero and less than --through")

    lines = args.cast.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise SystemExit(f"empty asciicast: {args.cast}")
    events = [json.loads(line) for line in lines[1:]]
    for event in events:
        retime_event(event, args.through, args.duration)

    rendered = [
        lines[0],
        *(json.dumps(event, separators=(",", ":")) for event in events),
    ]
    args.cast.write_text("\n".join(rendered) + "\n", encoding="utf-8")
    factor = args.through / args.duration
    print(
        f"Compressed the first {args.through:g}s to {args.duration:g}s "
        f"({factor:.3g}x); later intervals remain 1x."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
