"""Lifecycle management for the per-user hybrid router."""

from __future__ import annotations

import os
import plistlib
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .paths import (
    launchd_plist_path,
    router_log_path,
    router_pid_path,
    router_token_path,
    systemd_unit_path,
)
from .proxy import DEFAULT_PORT, LOCAL_TOKEN_HEADER, router_base_url
from .storage import atomic_write_text, ensure_private_dir

SERVICE_MARKER = "Managed by claude-openrouter hybrid routing"


def ensure_router_token() -> Path:
    path = router_token_path()
    if not path.exists():
        atomic_write_text(path, f"{secrets.token_urlsafe(32)}\n", 0o600)
    return path


def _serve_command(port: int) -> list[str]:
    executable = shutil.which("clor") or shutil.which("claude-openrouter")
    if executable:
        return [executable, "serve", "--port", str(port)]
    return [sys.executable, "-m", "claude_openrouter", "serve", "--port", str(port)]


def healthcheck(port: int = DEFAULT_PORT, timeout: float = 2.0) -> bool:
    try:
        token = router_token_path().read_text(encoding="utf-8").strip()
        request = urllib.request.Request(
            f"{router_base_url(port)}/healthz", headers={LOCAL_TOKEN_HEADER: token}
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _systemd_available() -> bool:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return False
    result = subprocess.run(
        [systemctl, "--user", "is-system-running"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode in {0, 1}


def _systemd_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _start_systemd(port: int) -> str:
    unit = systemd_unit_path()
    command = " ".join(_systemd_quote(part) for part in _serve_command(port))
    content = f"""# {SERVICE_MARKER}
[Unit]
Description=Claude OpenRouter hybrid model router
After=network-online.target

[Service]
Type=simple
ExecStart={command}
Restart=on-failure
RestartSec=2
UMask=0077
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=default.target
"""
    atomic_write_text(unit, content, 0o600)
    systemctl = shutil.which("systemctl")
    assert systemctl is not None
    for args in (["daemon-reload"], ["enable", "--now", unit.name], ["restart", unit.name]):
        result = subprocess.run(
            [systemctl, "--user", *args], capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"could not start the systemd user service: {detail}")
    return "systemd user service"


def _start_launchd(port: int) -> str:
    plist = launchd_plist_path()
    ensure_private_dir(plist.parent)
    ensure_private_dir(router_log_path().parent)
    document = {
        "Label": "io.github.xhluca.claude-openrouter",
        "ProgramArguments": _serve_command(port),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Background",
        "StandardOutPath": str(router_log_path()),
        "StandardErrorPath": str(router_log_path()),
    }
    atomic_write_text(plist, plistlib.dumps(document).decode(), 0o600)
    subprocess.run(["launchctl", "unload", str(plist)], check=False, capture_output=True)
    result = subprocess.run(
        ["launchctl", "load", str(plist)], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"could not start the launchd service: {result.stderr.strip()}")
    return "launchd user service"


def _read_pid() -> int | None:
    try:
        return int(router_pid_path().read_text(encoding="ascii").strip())
    except (FileNotFoundError, ValueError):
        return None


def _process_command(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
    except OSError:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, check=False
        )
        return result.stdout.strip() if result.returncode == 0 else ""


def _stop_fallback() -> bool:
    pid = _read_pid()
    if pid is None:
        return False
    command = _process_command(pid)
    if not command:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            router_pid_path().unlink(missing_ok=True)
            return False
        raise RuntimeError(f"could not verify that process {pid} belongs to the hybrid router")
    if command and not (
        ("claude-openrouter" in command or "claude_openrouter" in command or "clor" in command)
        and "serve" in command
    ):
        raise RuntimeError(f"refusing to stop unrecognized process {pid}")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    else:
        for _ in range(30):
            try:
                waited, _status = os.waitpid(pid, os.WNOHANG)
                if waited == pid:
                    break
            except ChildProcessError:
                pass
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(pid, signal.SIGKILL)
    router_pid_path().unlink(missing_ok=True)
    return True


def _start_fallback(port: int) -> str:
    _stop_fallback()
    ensure_private_dir(router_log_path().parent)
    with router_log_path().open("ab") as log:
        process = subprocess.Popen(
            _serve_command(port),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    atomic_write_text(router_pid_path(), f"{process.pid}\n", 0o600)
    return f"detached user process {process.pid}"


def start_service(port: int = DEFAULT_PORT) -> str:
    if not 1 <= port <= 65535:
        raise ValueError("router port must be between 1 and 65535")
    ensure_router_token()
    if sys.platform == "darwin" and shutil.which("launchctl"):
        mechanism = _start_launchd(port)
    elif _systemd_available():
        mechanism = _start_systemd(port)
    else:
        mechanism = _start_fallback(port)
    for _ in range(60):
        if healthcheck(port, timeout=0.2):
            return mechanism
        time.sleep(0.1)
    stop_service()
    raise RuntimeError(f"hybrid router did not become healthy on {router_base_url(port)}")


def stop_service() -> bool:
    stopped = False
    unit = systemd_unit_path()
    if unit.exists():
        content = unit.read_text(encoding="utf-8", errors="replace")
        if SERVICE_MARKER not in content:
            raise RuntimeError(f"refusing to remove unrecognized service unit: {unit}")
        systemctl = shutil.which("systemctl")
        if systemctl:
            subprocess.run(
                [systemctl, "--user", "disable", "--now", unit.name],
                capture_output=True,
                check=False,
            )
        unit.unlink()
        if systemctl:
            subprocess.run([systemctl, "--user", "daemon-reload"], check=False)
        stopped = True
    plist = launchd_plist_path()
    if plist.exists():
        content = plist.read_text(encoding="utf-8", errors="replace")
        if "io.github.xhluca.claude-openrouter" not in content:
            raise RuntimeError(f"refusing to remove unrecognized launchd plist: {plist}")
        if shutil.which("launchctl"):
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True, check=False)
        plist.unlink()
        stopped = True
    return _stop_fallback() or stopped
