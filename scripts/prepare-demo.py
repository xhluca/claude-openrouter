#!/usr/bin/env python3
"""Seed only Claude Code's non-secret onboarding/trust state for the demo."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: prepare-demo.py CLAUDE_CONFIG_DIR PROJECT_DIR")
    config_dir = Path(sys.argv[1]).resolve()
    project_dir = Path(sys.argv[2]).resolve()
    config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    document = {
        "numStartups": 1,
        "installMethod": "native",
        "autoUpdates": False,
        "hasCompletedOnboarding": True,
        "lastOnboardingVersion": "2.1.242",
        "opusProMigrationComplete": True,
        "sonnet1m45MigrationComplete": True,
        "announcementImpressions": {
            "fable-5-promo-2": 10,
            "fable-5-promo-2-2": 10,
            "fable-5-promo-2-3": 10,
            "fable-5-promo-2-4-max": 10,
            "opus-5-launch": 10,
        },
        "projects": {
            str(project_dir): {
                "allowedTools": ["WebFetch(domain:huggingface.co)"],
                "mcpContextUris": [],
                "mcpServers": {},
                "enabledMcpjsonServers": [],
                "disabledMcpjsonServers": [],
                "hasTrustDialogAccepted": True,
                "hasClaudeMdExternalIncludesApproved": False,
                "hasClaudeMdExternalIncludesWarningShown": False,
            }
        },
    }
    path = config_dir / ".claude.json"
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    settings = {
        "autoUpdates": False,
        "effortLevel": "low",
        "permissions": {"allow": ["WebFetch(domain:huggingface.co)"]},
        "theme": "dark",
    }
    settings_path = config_dir / "settings.json"
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    settings_path.chmod(0o600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
