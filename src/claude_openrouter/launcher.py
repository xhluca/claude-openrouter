"""Launch Claude Code against OpenRouter without replacing native authentication."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import NoReturn

from .openrouter import read_credential
from .paths import launch_settings_path
from .settings import BASE_URL

EXTERNAL_AUTH_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
)


def find_claude() -> str:
    executable = shutil.which("claude")
    if executable:
        return executable
    candidate = Path.home() / ".local" / "bin" / "claude"
    if candidate.is_file():
        return str(candidate)
    raise RuntimeError("Claude Code was not found; install it before running clor claude")


def _native_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in EXTERNAL_AUTH_ENV:
        environment.pop(name, None)
    return environment


def has_native_login(executable: str, environment: dict[str, str]) -> bool:
    try:
        result = subprocess.run(
            [executable, "auth", "status", "--json"],
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


def _without_authorization(headers: str | None) -> str:
    if not headers:
        return ""
    return "\n".join(
        line
        for line in headers.splitlines()
        if line.partition(":")[0].strip().casefold() != "authorization"
    ).strip()


def launch_claude(arguments: list[str]) -> NoReturn:
    executable = find_claude()
    settings = launch_settings_path()
    if not settings.is_file():
        raise RuntimeError(f"OpenRouter model settings not found at {settings}; run setup")

    key = read_credential()
    environment = _native_environment()
    existing_headers = _without_authorization(environment.get("ANTHROPIC_CUSTOM_HEADERS"))
    if existing_headers:
        environment["ANTHROPIC_CUSTOM_HEADERS"] = existing_headers
    else:
        environment.pop("ANTHROPIC_CUSTOM_HEADERS", None)
    environment["ANTHROPIC_BASE_URL"] = BASE_URL

    if has_native_login(executable, environment):
        authorization = f"Authorization: Bearer {key}"
        environment["ANTHROPIC_CUSTOM_HEADERS"] = "\n".join(
            part for part in (existing_headers, authorization) if part
        )
    else:
        environment["ANTHROPIC_AUTH_TOKEN"] = key
        if existing_headers:
            environment["ANTHROPIC_CUSTOM_HEADERS"] = existing_headers
        else:
            environment.pop("ANTHROPIC_CUSTOM_HEADERS", None)

    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    os.execvpe(
        executable,
        [executable, "--settings", str(settings), *arguments],
        environment,
    )
