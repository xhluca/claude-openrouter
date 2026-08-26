from __future__ import annotations

import json

import pytest
from conftest import write_json

from claude_openrouter import launcher
from claude_openrouter.openrouter import write_credential
from claude_openrouter.paths import launch_settings_path

KEY = "sk-or-v1-this-is-a-fake-test-key"


class Executed(Exception):
    pass


def _capture_exec(monkeypatch):
    captured = {}

    def execute(executable, arguments, environment):
        captured.update(
            executable=executable,
            arguments=arguments,
            environment=environment,
        )
        raise Executed

    monkeypatch.setattr(launcher.os, "execvpe", execute)
    return captured


def test_native_login_uses_custom_authorization_and_preserves_other_headers(
    isolated_home, monkeypatch
) -> None:
    write_credential(KEY)
    write_json(launch_settings_path(), {"model": "test/model"})
    monkeypatch.setattr(launcher, "find_claude", lambda: "/bin/claude")
    monkeypatch.setattr(launcher, "has_native_login", lambda *_args: True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "must-not-leak")
    monkeypatch.setenv(
        "ANTHROPIC_CUSTOM_HEADERS",
        "X-Trace: yes\nAuthorization: Bearer stale",
    )
    captured = _capture_exec(monkeypatch)

    with pytest.raises(Executed):
        launcher.launch_claude(["--continue"])

    environment = captured["environment"]
    assert captured["arguments"] == [
        "/bin/claude",
        "--settings",
        str(launch_settings_path()),
        "--continue",
    ]
    assert environment["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
    assert environment["ANTHROPIC_CUSTOM_HEADERS"] == (
        f"X-Trace: yes\nAuthorization: Bearer {KEY}"
    )
    assert "ANTHROPIC_API_KEY" not in environment
    assert "ANTHROPIC_AUTH_TOKEN" not in environment


def test_without_native_login_falls_back_to_external_token(
    isolated_home, monkeypatch
) -> None:
    write_credential(KEY)
    write_json(launch_settings_path(), {"model": "test/model"})
    monkeypatch.setattr(launcher, "find_claude", lambda: "/bin/claude")
    monkeypatch.setattr(launcher, "has_native_login", lambda *_args: False)
    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "Authorization: Bearer stale")
    captured = _capture_exec(monkeypatch)

    with pytest.raises(Executed):
        launcher.launch_claude(["--", "--print", "hello"])

    environment = captured["environment"]
    assert captured["arguments"][-2:] == ["--print", "hello"]
    assert environment["ANTHROPIC_AUTH_TOKEN"] == KEY
    assert "ANTHROPIC_API_KEY" not in environment
    assert "ANTHROPIC_CUSTOM_HEADERS" not in environment


def test_native_login_detection_reads_auth_status(monkeypatch) -> None:
    class Result:
        returncode = 0
        stdout = json.dumps(
            {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"}
        )

    monkeypatch.setattr(launcher.subprocess, "run", lambda *_args, **_kwargs: Result())
    assert launcher.has_native_login("/bin/claude", {}) is True


def test_launch_requires_generated_settings(isolated_home, monkeypatch) -> None:
    monkeypatch.setattr(launcher, "find_claude", lambda: "/bin/claude")
    with pytest.raises(RuntimeError, match="run setup"):
        launcher.launch_claude([])
