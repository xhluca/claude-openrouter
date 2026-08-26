from __future__ import annotations

import json

import pytest

from claude_openrouter import launcher


class Executed(Exception):
    pass


def test_compatibility_launcher_executes_plain_claude(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(launcher, "find_claude", lambda: "/bin/claude")

    def execute(executable, arguments):
        captured.update(executable=executable, arguments=arguments)
        raise Executed

    monkeypatch.setattr(launcher.os, "execv", execute)

    with pytest.raises(Executed):
        launcher.launch_claude(["--", "--continue"])

    assert captured == {
        "executable": "/bin/claude",
        "arguments": ["/bin/claude", "--continue"],
    }


def test_find_claude_reports_plain_command(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(launcher.shutil, "which", lambda _name: None)
    monkeypatch.setattr(launcher.Path, "home", lambda: tmp_path)

    with pytest.raises(RuntimeError, match="running claude"):
        launcher.find_claude()


def test_native_login_check_ignores_user_routing_settings(monkeypatch) -> None:
    class Result:
        returncode = 0
        stdout = json.dumps({"loggedIn": True, "authMethod": "claude.ai"})

    captured = {}
    monkeypatch.setattr(launcher, "find_claude", lambda: "/bin/claude")

    def run(command, **kwargs):
        captured.update(command=command, environment=kwargs["env"])
        return Result()

    monkeypatch.setattr(launcher.subprocess, "run", run)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "must-not-leak")

    assert launcher.has_native_login() is True
    assert captured["command"] == [
        "/bin/claude",
        "--setting-sources",
        "project,local",
        "auth",
        "status",
        "--json",
    ]
    assert "ANTHROPIC_AUTH_TOKEN" not in captured["environment"]
