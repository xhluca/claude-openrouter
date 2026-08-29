from __future__ import annotations

import os
import stat
import subprocess
import sys
from contextlib import suppress

import pytest

from claude_openrouter import service
from claude_openrouter.paths import (
    launchd_plist_path,
    router_log_path,
    router_pid_path,
    router_token_path,
    systemd_unit_path,
)


def test_router_token_is_private_and_stable(isolated_home) -> None:
    first = service.ensure_router_token()
    original = first.read_text()
    second = service.ensure_router_token()

    assert first == second == router_token_path()
    assert second.read_text() == original
    assert stat.S_IMODE(second.stat().st_mode) == 0o600


def test_serve_command_prefers_stable_user_shim(isolated_home, monkeypatch) -> None:
    shim = isolated_home / ".local" / "bin" / "clor"
    shim.parent.mkdir(parents=True)
    shim.write_text("shim")
    monkeypatch.setattr(
        service.shutil,
        "which",
        lambda _name: "/tmp/ephemeral-build/bin/clor",
    )

    assert service._serve_command(9417) == [str(shim), "serve", "--port", "9417"]


def test_fallback_process_is_started_and_stopped_safely(isolated_home, monkeypatch) -> None:
    command = [
        sys.executable,
        "-c",
        "import time; time.sleep(60)",
        "claude_openrouter",
        "serve",
    ]
    monkeypatch.setattr(service, "_serve_command", lambda _port: command)

    mechanism = service._start_fallback(9417)
    pid = int(router_pid_path().read_text())
    try:
        assert mechanism == f"detached user process {pid}"
        os.kill(pid, 0)
        assert service._stop_fallback() is True
        assert not router_pid_path().exists()
    finally:
        with suppress(ProcessLookupError):
            os.kill(pid, 9)


def test_systemd_unit_is_private_restartable_and_owned(isolated_home, monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(service, "_serve_command", lambda _port: ["/opt/clor", "serve"])
    monkeypatch.setattr(
        service.shutil,
        "which",
        lambda name: "/bin/systemctl" if name == "systemctl" else None,
    )
    monkeypatch.setattr(
        service.subprocess,
        "run",
        lambda command, **_kwargs: calls.append(command)
        or subprocess.CompletedProcess(command, 0, "", ""),
    )

    assert service._start_systemd(9417) == "systemd user service"
    content = systemd_unit_path().read_text()
    assert service.SERVICE_MARKER in content
    assert "Restart=on-failure" in content
    assert 'ExecStart="/opt/clor" "serve"' in content
    assert stat.S_IMODE(systemd_unit_path().stat().st_mode) == 0o600
    assert calls[-1][-2:] == ["restart", "claude-openrouter.service"]


def test_launchd_uses_bootstrap_in_the_gui_domain(isolated_home, monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(service.os, "getuid", lambda: 501)
    monkeypatch.setattr(service, "_serve_command", lambda _port: ["/opt/clor", "serve"])
    monkeypatch.setattr(
        service.shutil,
        "which",
        lambda name: "/bin/launchctl" if name == "launchctl" else None,
    )

    def run(command, **_kwargs):
        calls.append(command)
        returncode = 0 if command[1:3] == ["print", "gui/501"] else 3
        if command[1] == "bootstrap":
            returncode = 0
        return subprocess.CompletedProcess(command, returncode, "", "")

    monkeypatch.setattr(service.subprocess, "run", run)

    assert service._start_launchd(9417) == "launchd user service"
    assert ["/bin/launchctl", "bootout", f"gui/501/{service.LAUNCHD_LABEL}"] in calls
    assert [
        "/bin/launchctl",
        "bootstrap",
        "gui/501",
        str(launchd_plist_path()),
    ] in calls
    assert not any("load" in call or "unload" in call for call in calls)
    assert stat.S_IMODE(launchd_plist_path().stat().st_mode) == 0o600
    assert stat.S_IMODE(router_log_path().stat().st_mode) == 0o600


def test_startup_failure_includes_router_log(isolated_home, monkeypatch) -> None:
    monkeypatch.setattr(service, "_systemd_available", lambda: False)
    monkeypatch.setattr(service, "_start_fallback", lambda _port: "test process")
    monkeypatch.setattr(service, "healthcheck", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(service, "stop_service", lambda: True)
    monkeypatch.setattr(service, "STARTUP_TIMEOUT_SECONDS", 0)
    router_log_path().parent.mkdir(parents=True)
    router_log_path().write_text("bind failed: address already in use\n")

    with pytest.raises(RuntimeError, match="address already in use"):
        service.start_service(9417)
