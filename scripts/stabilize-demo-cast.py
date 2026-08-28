#!/usr/bin/env python3
"""Replace the final TUI delta with a clean full-screen terminal snapshot."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SNAPSHOT_MARKER = "\x1b]777;claude-openrouter-demo-snapshot\x07"


def output_text(event: Any) -> str:
    if (
        isinstance(event, list)
        and len(event) == 3
        and event[1] == "o"
        and isinstance(event[2], str)
    ):
        return event[2]
    return ""


def replay(cast: Path) -> int:
    lines = cast.read_text(encoding="utf-8").splitlines()[1:]
    for line in lines:
        event = json.loads(line)
        output = output_text(event)
        if output:
            os.write(sys.stdout.fileno(), output.encode())
    os.write(sys.stdout.fileno(), b"\x1b[?25h")
    time.sleep(10)
    return 0


def capture_snapshot(cast: Path, width: int, height: int) -> str:
    socket = f"clor-demo-snapshot-{os.getpid()}"
    replay_command = shlex.join(
        [sys.executable, str(Path(__file__).resolve()), "--replay", str(cast)]
    )
    env = {**os.environ, "TERM": "xterm-256color"}
    subprocess.run(
        [
            "tmux",
            "-L",
            socket,
            "new-session",
            "-d",
            "-x",
            str(width),
            "-y",
            str(height),
            replay_command,
        ],
        check=True,
        env=env,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            plain = subprocess.run(
                ["tmux", "-L", socket, "capture-pane", "-p", "-t", "0"],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            ).stdout
            if "? for shortcuts" in plain:
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("replayed Claude screen did not become ready")
        snapshot = subprocess.run(
            ["tmux", "-L", socket, "capture-pane", "-p", "-e", "-t", "0"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        ).stdout
    finally:
        subprocess.run(
            ["tmux", "-L", socket, "kill-server"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    return snapshot.rstrip("\n")


def stabilize(cast: Path, snapshot_path: Path | None) -> int:
    lines = cast.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise SystemExit(f"empty asciicast: {cast}")
    header = json.loads(lines[0])
    events = [json.loads(line) for line in lines[1:]]
    existing_snapshot = next(
        (
            output_text(event)
            for event in reversed(events)
            if SNAPSHOT_MARKER in output_text(event)
        ),
        None,
    )
    events = [event for event in events if SNAPSHOT_MARKER not in output_text(event)]
    if snapshot_path is not None:
        snapshot = snapshot_path.read_text(encoding="utf-8").rstrip("\n")
    elif existing_snapshot is not None:
        snapshot = existing_snapshot.split("\x1b[H", 1)[1]
        snapshot = snapshot.removesuffix("\x1b[?7h\x1b[?25h")
        snapshot = snapshot.removesuffix("\x1b[?25h")
        snapshot = snapshot.replace("\r\n", "\n").rstrip("\n")
    else:
        snapshot = capture_snapshot(cast, int(header["width"]), int(header["height"]))
    timestamp = round(float(events[-1][0]) + 0.001, 6)
    payload = (
        SNAPSHOT_MARKER
        + "\x1b[?25l\x1b[?7l\x1b[2J\x1b[H"
        + snapshot.replace("\n", "\r\n")
        + "\x1b[?7h\x1b[?25h"
    )
    events.append([timestamp, "o", payload])
    rendered = [
        json.dumps(header, separators=(",", ":")),
        *(json.dumps(event, separators=(",", ":")) for event in events),
    ]
    cast.write_text("\n".join(rendered) + "\n", encoding="utf-8")
    print(f"Stabilized the final {header['width']}x{header['height']} Claude frame.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cast", type=Path)
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    if args.replay:
        return replay(args.cast)
    return stabilize(args.cast, args.snapshot)


if __name__ == "__main__":
    raise SystemExit(main())
