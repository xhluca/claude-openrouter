"""Claude Code settings integration and reversible state management."""

from __future__ import annotations

import shutil
import stat
from pathlib import Path
from typing import Any

from .models import picker_row
from .paths import (
    backup_path,
    cache_dir,
    claude_settings_path,
    config_dir,
    credential_path,
    helper_path,
    launch_settings_path,
    preferences_path,
    state_dir,
)
from .storage import atomic_write_json, atomic_write_text, read_json_object

BASE_URL = "https://openrouter.ai/api"
ROOT_FIELDS = ("apiKeyHelper", "modelPicker", "model")
ENV_FIELDS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def _field_snapshot(document: dict[str, Any], field: str) -> dict[str, Any]:
    if field in document:
        return {"present": True, "value": document[field]}
    return {"present": False}


def _capture_backup(settings: dict[str, Any], existed: bool) -> None:
    path = backup_path()
    if path.exists():
        backup = read_json_object(path)
        if backup.get("settings_path") != str(claude_settings_path()):
            raise RuntimeError(
                "an existing claude-openrouter backup belongs to a different "
                "CLAUDE_CONFIG_DIR; reset it from that environment first"
            )
        return
    env = settings.get("env")
    env_object = env if isinstance(env, dict) else {}
    atomic_write_json(
        path,
        {
            "version": 1,
            "settings_path": str(claude_settings_path()),
            "settings_existed": existed,
            "root": {field: _field_snapshot(settings, field) for field in ROOT_FIELDS},
            "env_was_object": isinstance(env, dict),
            "env": {field: _field_snapshot(env_object, field) for field in ENV_FIELDS},
        },
    )


def write_key_helper() -> Path:
    path = helper_path()
    content = """#!/bin/sh
set -eu
helper_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
IFS= read -r openrouter_key < "$helper_dir/credential"
printf '%s\\n' "$openrouter_key"
"""
    atomic_write_text(path, content, 0o700)
    return path


def load_preferences() -> dict[str, Any]:
    document = read_json_object(preferences_path(), missing_ok=True)
    favorites = document.get("favorites", [])
    if not isinstance(favorites, list) or not all(isinstance(item, str) for item in favorites):
        raise RuntimeError(f"invalid favorites in {preferences_path()}")
    return document


def favorite_ids() -> list[str]:
    return list(load_preferences().get("favorites", []))


def save_preferences(models: list[dict[str, Any]], default_model: str) -> None:
    atomic_write_json(
        preferences_path(),
        {
            "version": 1,
            "favorites": [str(model["id"]) for model in models],
            "default_model": default_model,
        },
    )


def configure_claude(models: list[dict[str, Any]]) -> Path:
    """Write launch-scoped model settings without taking over native Claude auth."""
    if not models:
        raise ValueError("select at least one model")
    migrate_legacy_settings()
    old_preferences = load_preferences()
    old_default = old_preferences.get("default_model")
    ids = [str(model["id"]) for model in models]
    default_model = old_default if isinstance(old_default, str) and old_default in ids else ids[0]
    settings = {
        "modelPicker": {
            "options": [picker_row(model) for model in models],
            "replaceBuiltInOptions": True,
        },
        "model": default_model,
    }
    path = launch_settings_path()
    atomic_write_json(path, settings)
    save_preferences(models, default_model)
    return path


def _restore_snapshot(document: dict[str, Any], field: str, snapshot: dict[str, Any]) -> None:
    if snapshot.get("present"):
        document[field] = snapshot.get("value")
    else:
        document.pop(field, None)


def _looks_managed_picker(value: Any) -> bool:
    if not isinstance(value, dict) or value.get("replaceBuiltInOptions") is not True:
        return False
    options = value.get("options")
    return isinstance(options, list) and any(
        isinstance(row, dict)
        and "OpenRouter via claude-openrouter" in str(row.get("description", ""))
        for row in options
    )


def restore_claude_settings() -> bool:
    path = claude_settings_path()
    if not path.exists():
        return False
    settings = read_json_object(path)
    backup_file = backup_path()

    if backup_file.exists():
        backup = read_json_object(backup_file)
        if backup.get("settings_path") != str(path):
            raise RuntimeError(
                "the saved Claude settings backup belongs to a different CLAUDE_CONFIG_DIR"
            )
        root = backup.get("root")
        env_backup = backup.get("env")
        if not isinstance(root, dict) or not isinstance(env_backup, dict):
            raise RuntimeError(f"invalid settings backup at {backup_file}")
        for field in ROOT_FIELDS:
            snapshot = root.get(field)
            if not isinstance(snapshot, dict):
                raise RuntimeError(f"invalid settings backup field {field}")
            _restore_snapshot(settings, field, snapshot)

        env = settings.get("env")
        if env is not None and not isinstance(env, dict):
            raise RuntimeError(f"the env setting in {path} must be a JSON object")
        env_object = dict(env or {})
        for field in ENV_FIELDS:
            snapshot = env_backup.get(field)
            if not isinstance(snapshot, dict):
                raise RuntimeError(f"invalid environment backup field {field}")
            _restore_snapshot(env_object, field, snapshot)
        if env_object or backup.get("env_was_object"):
            settings["env"] = env_object
        else:
            settings.pop("env", None)
    else:
        managed_helper = settings.get("apiKeyHelper") == str(helper_path().resolve())
        managed_picker = _looks_managed_picker(settings.get("modelPicker"))
        picker = settings.get("modelPicker")
        picker_options = picker.get("options", []) if isinstance(picker, dict) else []
        managed_model_ids = {
            row.get("model") for row in picker_options if isinstance(row, dict)
        }
        managed_model = settings.get("model") in managed_model_ids
        env = settings.get("env")
        managed_base = (
            isinstance(env, dict) and env.get("ANTHROPIC_BASE_URL") == BASE_URL
        )
        if not (managed_helper or managed_picker or managed_base):
            return False
        if managed_helper:
            settings.pop("apiKeyHelper", None)
        if managed_picker:
            settings.pop("modelPicker", None)
        if managed_model:
            settings.pop("model", None)
        if isinstance(env, dict) and (
            managed_helper or managed_picker or env.get("ANTHROPIC_BASE_URL") == BASE_URL
        ):
            env = dict(env)
            if env.get("ANTHROPIC_BASE_URL") == BASE_URL:
                env.pop("ANTHROPIC_BASE_URL", None)
            for field in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
                if env.get(field) == "":
                    env.pop(field, None)
            if env:
                settings["env"] = env
            else:
                settings.pop("env", None)

    if settings:
        atomic_write_json(path, settings)
    else:
        path.unlink()
    return True


def migrate_legacy_settings() -> bool:
    """Restore the pre-0.2 global integration and retire its auth helper."""
    legacy_present = backup_path().exists() or helper_path().exists()
    if not legacy_present and claude_settings_path().exists():
        settings = read_json_object(claude_settings_path())
        legacy_present = (
            settings.get("apiKeyHelper") == str(helper_path().resolve())
            or _looks_managed_picker(settings.get("modelPicker"))
            or (
                isinstance(settings.get("env"), dict)
                and settings["env"].get("ANTHROPIC_BASE_URL") == BASE_URL
            )
        )
    if not legacy_present:
        return False
    restored = restore_claude_settings()
    backup_path().unlink(missing_ok=True)
    helper_path().unlink(missing_ok=True)
    return restored


def _remove_private_tree(path: Path) -> None:
    if not path.exists():
        return
    resolved = path.resolve()
    home = Path.home().resolve()
    if resolved in {home, Path("/")} or len(resolved.parts) < 3:
        raise RuntimeError(f"refusing to remove unsafe path: {resolved}")
    shutil.rmtree(resolved)


def reset_integration() -> bool:
    restored = restore_claude_settings()
    for directory in (config_dir(), cache_dir(), state_dir()):
        _remove_private_tree(directory)
    return restored


def assert_private_files() -> None:
    """Raise if a secret-bearing file is accessible by group or other users."""
    for path in (
        credential_path(),
        preferences_path(),
        helper_path(),
        launch_settings_path(),
    ):
        if path.exists() and stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise RuntimeError(f"insecure permissions on {path}")
