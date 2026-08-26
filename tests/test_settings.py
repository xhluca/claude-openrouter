from __future__ import annotations

import json

from conftest import write_json

from claude_openrouter.openrouter import write_credential
from claude_openrouter.paths import (
    backup_path,
    claude_settings_path,
    config_dir,
    helper_path,
)
from claude_openrouter.settings import configure_claude, reset_integration

KEY = "sk-or-v1-this-is-a-fake-test-key"


def read_settings() -> dict:
    return json.loads(claude_settings_path().read_text())


def test_configure_uses_helper_and_native_model_picker(
    isolated_home, sample_models
) -> None:
    original = {
        "theme": "dark",
        "model": "sonnet",
        "env": {"KEEP": "yes", "ANTHROPIC_BASE_URL": "https://gateway.example"},
        "modelPicker": {"options": [{"model": "old"}]},
    }
    write_json(claude_settings_path(), original)
    write_credential(KEY)

    configure_claude([sample_models[1], sample_models[2]])
    settings = read_settings()
    assert settings["theme"] == "dark"
    assert settings["env"]["KEEP"] == "yes"
    assert settings["env"]["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
    assert settings["env"]["ANTHROPIC_API_KEY"] == ""
    assert settings["env"]["ANTHROPIC_AUTH_TOKEN"] == ""
    assert KEY not in claude_settings_path().read_text()
    assert settings["apiKeyHelper"] == str(helper_path().resolve())
    assert helper_path().stat().st_mode & 0o777 == 0o700
    assert settings["model"] == "anthropic/claude-opus-4.6"
    assert settings["modelPicker"]["replaceBuiltInOptions"] is True
    assert [row["model"] for row in settings["modelPicker"]["options"]] == [
        "anthropic/claude-opus-4.6",
        "google/gemini-3.1-pro-preview",
    ]
    assert backup_path().exists()


def test_reset_restores_owned_fields_and_keeps_later_unrelated_edits(
    isolated_home, sample_models
) -> None:
    original = {
        "theme": "dark",
        "model": "sonnet",
        "env": {"KEEP": "yes", "ANTHROPIC_API_KEY": "original-key"},
    }
    write_json(claude_settings_path(), original)
    write_credential(KEY)
    configure_claude(sample_models[:2])

    changed = read_settings()
    changed["verbose"] = True
    changed["env"]["LATER"] = "preserved"
    write_json(claude_settings_path(), changed)

    assert reset_integration() is True
    restored = read_settings()
    assert restored == {
        **original,
        "verbose": True,
        "env": {
            "KEEP": "yes",
            "ANTHROPIC_API_KEY": "original-key",
            "LATER": "preserved",
        },
    }
    assert not config_dir().exists()
    assert not backup_path().exists()


def test_reset_removes_settings_created_from_absent_state(
    isolated_home, sample_models
) -> None:
    write_credential(KEY)
    configure_claude(sample_models[:1])
    assert claude_settings_path().exists()
    reset_integration()
    assert not claude_settings_path().exists()


def test_repeated_configuration_keeps_first_backup(isolated_home, sample_models) -> None:
    write_json(claude_settings_path(), {"model": "original"})
    write_credential(KEY)
    configure_claude(sample_models[:1])
    first_backup = backup_path().read_text()
    configure_claude(sample_models[2:])
    assert backup_path().read_text() == first_backup
    reset_integration()
    assert read_settings() == {"model": "original"}
