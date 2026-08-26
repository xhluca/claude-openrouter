"""Backward-compatible launcher for the ordinary Claude Code command."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import NoReturn


def find_claude() -> str:
    executable = shutil.which("claude")
    if executable:
        return executable
    candidate = Path.home() / ".local" / "bin" / "claude"
    if candidate.is_file():
        return str(candidate)
    raise RuntimeError("Claude Code was not found; install it before running claude")


def has_native_login() -> bool:
    """Check native OAuth without loading clor's user-level routing settings."""
    try:
        executable = find_claude()
    except RuntimeError:
        return False
    environment = dict(os.environ)
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
        environment.pop(name, None)
    try:
        result = subprocess.run(
            [
                executable,
                "--setting-sources",
                "project,local",
                "auth",
                "status",
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=environment,
        )
        status = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return False
    return (
        result.returncode == 0
        and isinstance(status, dict)
        and status.get("loggedIn") is True
        and status.get("authMethod") == "claude.ai"
    )


def launch_claude(arguments: list[str]) -> NoReturn:
    """Exec Claude Code; retained so older ``clor claude`` commands keep working."""
    executable = find_claude()
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    os.execv(executable, [executable, *arguments])
