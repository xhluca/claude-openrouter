from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from claude_openrouter import __version__

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


def test_installer_release_matches_package_version() -> None:
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert f'package_version="{__version__}"' in installer
    assert 'wheel_sha256="TO_BE_REPLACED"' not in installer


def test_uv_installs_use_copy_mode_to_avoid_cross_filesystem_warnings() -> None:
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    commands = [line.strip() for line in installer.splitlines() if "uv tool install" in line]

    assert commands
    assert all("--link-mode copy" in command for command in commands)


def test_registry_install_refreshes_stale_package_metadata() -> None:
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    registry_command = next(
        line
        for line in installer.splitlines()
        if "uv tool install" in line and "package_name" in line
    )

    assert '--refresh-package "$package_name"' in registry_command


def test_readme_keeps_acknowledgements_at_bottom() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.rstrip().endswith("management spectrum.")
    assert "Claude Code Router (CCR)" in readme


def test_pages_source_keeps_short_installer_and_plain_claude_command() -> None:
    site = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    assert "https://xhluca.github.io/claude-openrouter/install.sh" in site
    assert "plain <code>claude</code>" in site
    assert "<code>clor claude</code>" not in site
    assert "assets/asciinema-player.min.js" in site
    assert 'data-demo-action="replay"' in site


def test_demo_uses_plain_claude_and_current_settings_path() -> None:
    cast = (ROOT / "docs" / "assets" / "demo.cast").read_text(encoding="utf-8")
    header = json.loads(cast.splitlines()[0])

    assert (header["width"], header["height"]) == (75, 24)
    assert "clor claude" not in cast
    assert "claude-settings.json" not in cast
    assert ".claude/settings.json" in cast
    assert re.search(r"claude-openrouter \d+\.\d+\.\d+", cast)
    assert "I've verified. Now let me answer briefly." not in cast
    assert "The user is asking what model powers me" not in cast
    assert "The user asked two things: what model powers me" not in cast


def test_demo_submits_model_command_without_partial_autocomplete_frames() -> None:
    capture = (ROOT / "scripts" / "capture-demo.exp").read_text(encoding="utf-8")
    assert 'submit_tui "/model"' in capture
    assert 'type_tui "/model"' not in capture
