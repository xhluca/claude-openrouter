from __future__ import annotations

import json

from conftest import write_json

from claude_openrouter.openrouter import write_credential
from claude_openrouter.paths import (
    backup_path,
    claude_settings_path,
    config_dir,
    helper_path,
    launch_settings_path,
)
from claude_openrouter.settings import (
    BASE_URL,
    _capture_backup,
    configure_claude,
    reset_integration,
    write_key_helper,
)

KEY = "sk-or-v1-this-is-a-fake-test-key"


def read_json(path) -> dict:
    return json.loads(path.read_text())


def test_configure_uses_launch_scoped_settings_and_leaves_native_auth_alone(
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

    result = configure_claude([sample_models[1], sample_models[2]])

    assert result == launch_settings_path()
    assert read_json(claude_settings_path()) == original
    assert KEY not in launch_settings_path().read_text()
    assert not helper_path().exists()
    assert not backup_path().exists()
    launch_settings = read_json(launch_settings_path())
    assert launch_settings["model"] == "anthropic/claude-opus-4.6"
    assert launch_settings["modelPicker"]["replaceBuiltInOptions"] is True
    assert [row["model"] for row in launch_settings["modelPicker"]["options"]] == [
        "anthropic/claude-opus-4.6",
        "google/gemini-3.1-pro-preview",
    ]


def test_configure_migrates_legacy_global_settings_from_backup(
    isolated_home, sample_models
) -> None:
    original = {
        "theme": "dark",
        "model": "sonnet",
        "env": {"KEEP": "yes", "ANTHROPIC_API_KEY": "original-key"},
    }
    write_json(claude_settings_path(), original)
    _capture_backup(original, True)
    write_key_helper()
    legacy = {
        "theme": "dark",
        "model": sample_models[0]["id"],
        "apiKeyHelper": str(helper_path().resolve()),
        "modelPicker": {
            "replaceBuiltInOptions": True,
            "options": [
                {
                    "model": sample_models[0]["id"],
                    "description": "OpenRouter via claude-openrouter",
                }
            ],
        },
        "env": {
            "KEEP": "yes",
            "LATER": "preserved",
            "ANTHROPIC_BASE_URL": BASE_URL,
            "ANTHROPIC_API_KEY": "",
            "ANTHROPIC_AUTH_TOKEN": "",
        },
        "verbose": True,
    }
    write_json(claude_settings_path(), legacy)
    write_credential(KEY)

    configure_claude(sample_models[:2])

    assert read_json(claude_settings_path()) == {
        **original,
        "verbose": True,
        "env": {
            "KEEP": "yes",
            "ANTHROPIC_API_KEY": "original-key",
            "LATER": "preserved",
        },
    }
    assert not backup_path().exists()
    assert not helper_path().exists()


def test_configure_cleans_recognizable_legacy_settings_without_backup(
    isolated_home, sample_models
) -> None:
    legacy = {
        "theme": "dark",
        "model": sample_models[0]["id"],
        "apiKeyHelper": str(helper_path().resolve()),
        "modelPicker": {
            "replaceBuiltInOptions": True,
            "options": [
                {
                    "model": sample_models[0]["id"],
                    "description": "OpenRouter via claude-openrouter",
                }
            ],
        },
        "env": {
            "KEEP": "yes",
            "ANTHROPIC_BASE_URL": BASE_URL,
            "ANTHROPIC_API_KEY": "",
            "ANTHROPIC_AUTH_TOKEN": "",
        },
    }
    write_json(claude_settings_path(), legacy)
    write_credential(KEY)

    configure_claude(sample_models[:1])

    assert read_json(claude_settings_path()) == {"theme": "dark", "env": {"KEEP": "yes"}}


def test_reset_removes_tool_data_without_changing_native_settings(
    isolated_home, sample_models
) -> None:
    original = {"theme": "dark", "model": "sonnet"}
    write_json(claude_settings_path(), original)
    write_credential(KEY)
    configure_claude(sample_models[:1])

    assert reset_integration() is False
    assert read_json(claude_settings_path()) == original
    assert not config_dir().exists()


def test_reset_restores_unmigrated_legacy_integration(
    isolated_home, sample_models
) -> None:
    original = {"model": "sonnet"}
    write_json(claude_settings_path(), original)
    _capture_backup(original, True)
    legacy = {
        "model": sample_models[0]["id"],
        "apiKeyHelper": str(helper_path().resolve()),
        "env": {"ANTHROPIC_BASE_URL": BASE_URL},
    }
    write_json(claude_settings_path(), legacy)
    write_credential(KEY)

    assert reset_integration() is True
    assert read_json(claude_settings_path()) == original
    assert not config_dir().exists()
