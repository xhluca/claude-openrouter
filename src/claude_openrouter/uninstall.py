"""Best-effort removal of the installed package after integration reset."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PACKAGE_NAME = "claude-openrouter"


def _fallback_tool_dir() -> Path:
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data_home / PACKAGE_NAME / "tool"


def _bin_dir() -> Path:
    return Path(os.environ.get("XDG_BIN_HOME", Path.home() / ".local" / "bin"))


def _remove_fallback_install() -> bool:
    tool_dir = _fallback_tool_dir()
    try:
        running_inside = Path(sys.executable).resolve().is_relative_to(tool_dir.resolve())
    except FileNotFoundError:
        running_inside = False
    if not tool_dir.exists() and not running_inside:
        return False
    for command in ("claude-openrouter", "clor"):
        link = _bin_dir() / command
        if link.is_symlink() and tool_dir.resolve() in link.resolve().parents:
            link.unlink()
    if tool_dir.exists():
        shutil.rmtree(tool_dir)
    parent = tool_dir.parent
    if parent.exists() and not any(parent.iterdir()):
        parent.rmdir()
    return True


def remove_installed_package() -> bool:
    uv = shutil.which("uv")
    if uv:
        listing = subprocess.run(
            [uv, "tool", "list"],
            check=False,
            capture_output=True,
            text=True,
        )
        if re.search(r"(?m)^claude-openrouter\s+v", listing.stdout):
            removed = subprocess.run(
                [uv, "tool", "uninstall", PACKAGE_NAME], check=False
            )
            return removed.returncode == 0
    pipx = shutil.which("pipx")
    if pipx:
        listing = subprocess.run(
            [pipx, "list", "--short"],
            check=False,
            capture_output=True,
            text=True,
        )
        if re.search(r"(?m)^claude-openrouter\s", listing.stdout):
            removed = subprocess.run([pipx, "uninstall", PACKAGE_NAME], check=False)
            return removed.returncode == 0
    # Plain pip and development installs are intentionally not guessed: removing
    # from an arbitrary Python environment can damage unrelated applications.
    return _remove_fallback_install()
