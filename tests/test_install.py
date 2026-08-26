from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_installer_is_valid_posix_shell() -> None:
    subprocess.run(["sh", "-n", str(ROOT / "install.sh")], check=True)


def test_installer_help_does_not_require_network() -> None:
    result = subprocess.run(
        ["sh", str(ROOT / "install.sh"), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "never accepts an API key as a command-line argument" in result.stdout
    assert "--install-only" in result.stdout


def test_uv_installs_use_copy_mode_to_avoid_cross_filesystem_warnings() -> None:
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    commands = [line.strip() for line in installer.splitlines() if "uv tool install" in line]

    assert commands
    assert all("--link-mode copy" in command for command in commands)


def test_readme_keeps_acknowledgements_at_bottom() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.rstrip().endswith("management spectrum.")
    assert "Claude Code Router (CCR)" in readme
