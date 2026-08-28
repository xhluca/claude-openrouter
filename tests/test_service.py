from __future__ import annotations

import os
import stat
import subprocess
import sys
from contextlib import suppress

from claude_openrouter import service
from claude_openrouter.paths import router_pid_path, router_token_path, systemd_unit_path


def test_router_token_is_private_and_stable(isolated_home) -> None:
    first = service.ensure_router_token()
    original = first.read_text()
    second = service.ensure_router_token()

    assert first == second == router_token_path()
    assert second.read_text() == original
    assert stat.S_IMODE(second.stat().st_mode) == 0o600


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
