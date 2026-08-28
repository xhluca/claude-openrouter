"""Filesystem locations used by claude-openrouter."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "claude-openrouter"


def config_dir() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / APP_NAME


def cache_dir() -> Path:
    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / APP_NAME


def state_dir() -> Path:
    root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return root / APP_NAME


def claude_config_dir() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(override).expanduser() if override else Path.home() / ".claude"


def claude_settings_path() -> Path:
    return claude_config_dir() / "settings.json"


def credential_path() -> Path:
    return config_dir() / "credential"


def anthropic_credential_path() -> Path:
    return config_dir() / "anthropic-credential"


def router_token_path() -> Path:
    return config_dir() / "router-token"


def launch_settings_path() -> Path:
    return config_dir() / "claude-settings.json"


def helper_path() -> Path:
    return config_dir() / "api-key-helper.sh"


def preferences_path() -> Path:
    return config_dir() / "config.json"


def catalog_path() -> Path:
    return cache_dir() / "models.json"


def backup_path() -> Path:
    return state_dir() / "claude-settings-backup.json"


def router_pid_path() -> Path:
    return state_dir() / "router.pid"


def router_log_path() -> Path:
    return state_dir() / "router.log"


def router_status_path() -> Path:
    return state_dir() / "router-status.json"


def systemd_unit_path() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "systemd" / "user" / "claude-openrouter.service"


def launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "io.github.xhluca.claude-openrouter.plist"
