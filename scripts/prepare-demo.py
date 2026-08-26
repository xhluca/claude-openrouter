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
        "projects": {
            str(project_dir): {
                "allowedTools": [],
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

