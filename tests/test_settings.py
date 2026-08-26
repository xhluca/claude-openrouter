from __future__ import annotations

import json
import stat

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
    configure_claude,
    refresh_claude_credential,
    reset_integration,
    write_key_helper,
)

KEY = "sk-or-v1-this-is-a-fake-test-key"
NEW_KEY = "sk-or-v1-this-is-a-new-fake-test-key"


def read_json(path) -> dict:
    return json.loads(path.read_text())


def write_legacy_backup(original: dict, *, existed: bool = True) -> None:
    env = original.get("env")
    env_object = env if isinstance(env, dict) else {}

    def snapshot(document, field):
        return (
            {"present": True, "value": document[field]}
            if field in document
            else {"present": False}
        )

    write_json(
        backup_path(),
        {
            "version": 1,
            "settings_path": str(claude_settings_path()),
            "settings_existed": existed,
            "root": {
                field: snapshot(original, field)
                for field in ("apiKeyHelper", "modelPicker", "model")
            },
            "env_was_object": isinstance(env, dict),
            "env": {
                field: snapshot(env_object, field)
                for field in (
                    "ANTHROPIC_BASE_URL",
                    "ANTHROPIC_API_KEY",
                    "ANTHROPIC_AUTH_TOKEN",
                )
            },
        },
    )


def test_configure_makes_plain_claude_use_openrouter_and_preserves_native_auth(
    isolated_home, sample_models
) -> None:
    original = {
        "theme": "dark",
        "model": "sonnet",
        "apiKeyHelper": "/original/helper",
        "env": {
            "KEEP": "yes",
            "ANTHROPIC_BASE_URL": "https://gateway.example",
            "ANTHROPIC_API_KEY": "original-key",
            "ANTHROPIC_AUTH_TOKEN": "original-token",
            "ANTHROPIC_CUSTOM_HEADERS": "X-Trace: yes\nAuthorization: Bearer old",
        },
        "modelPicker": {"options": [{"model": "old"}]},
    }
    write_json(claude_settings_path(), original)
    write_credential(KEY)

    result = configure_claude([sample_models[1], sample_models[2]])

    assert result == claude_settings_path()
    settings = read_json(claude_settings_path())
    assert settings["theme"] == "dark"
    assert settings["model"] == "anthropic/claude-opus-4.6"
    assert "apiKeyHelper" not in settings
    assert settings["env"] == {
        "KEEP": "yes",
        "ANTHROPIC_BASE_URL": BASE_URL,
        "ANTHROPIC_CUSTOM_HEADERS": f"X-Trace: yes\nAuthorization: Bearer {KEY}",
    }
    assert settings["modelPicker"]["replaceBuiltInOptions"] is True
    assert [row["model"] for row in settings["modelPicker"]["options"]] == [
        "anthropic/claude-opus-4.6",
        "google/gemini-3.1-pro-preview",
    ]
    assert stat.S_IMODE(claude_settings_path().stat().st_mode) == 0o600
    assert read_json(backup_path())["version"] == 2
    assert not launch_settings_path().exists()
    assert not helper_path().exists()

    assert reset_integration() is True
    assert read_json(claude_settings_path()) == original


def test_reconfigure_keeps_original_backup_and_does_not_duplicate_authorization(
    isolated_home, sample_models
) -> None:
    original = {
        "theme": "dark",
        "env": {"ANTHROPIC_CUSTOM_HEADERS": "X-Trace: yes"},
    }
    write_json(claude_settings_path(), original)
    write_credential(KEY)
    configure_claude(sample_models[:2])
    backup = backup_path().read_text()

    configure_claude(sample_models[2:])

    assert backup_path().read_text() == backup
    headers = read_json(claude_settings_path())["env"]["ANTHROPIC_CUSTOM_HEADERS"]
    assert headers == f"X-Trace: yes\nAuthorization: Bearer {KEY}"

    reset_integration()
    assert read_json(claude_settings_path()) == original


def test_config_updates_the_persistent_authorization_header(
    isolated_home, sample_models
) -> None:
    write_credential(KEY)
    configure_claude(sample_models[:1])

    assert refresh_claude_credential(NEW_KEY) is True
    settings = read_json(claude_settings_path())
    assert settings["env"]["ANTHROPIC_CUSTOM_HEADERS"] == (
        f"Authorization: Bearer {NEW_KEY}"
    )


def test_configure_without_native_login_uses_token_fallback(
    isolated_home, sample_models
) -> None:
    write_json(
        claude_settings_path(),
        {"env": {"ANTHROPIC_CUSTOM_HEADERS": "X-Trace: yes\nAuthorization: stale"}},
    )
    write_credential(KEY)

    configure_claude(sample_models[:1], native_login=False)

    env = read_json(claude_settings_path())["env"]
    assert env["ANTHROPIC_AUTH_TOKEN"] == KEY
    assert env["ANTHROPIC_CUSTOM_HEADERS"] == "X-Trace: yes"

    assert refresh_claude_credential(NEW_KEY) is True
    assert read_json(claude_settings_path())["env"]["ANTHROPIC_AUTH_TOKEN"] == NEW_KEY


def test_configure_migrates_legacy_global_settings_from_backup(
    isolated_home, sample_models
) -> None:
    original = {
        "theme": "dark",
        "model": "sonnet",
        "env": {"KEEP": "yes", "ANTHROPIC_API_KEY": "original-key"},
    }
    write_json(claude_settings_path(), original)
    write_legacy_backup(original)
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

    settings = read_json(claude_settings_path())
    assert settings["theme"] == "dark"
    assert settings["verbose"] is True
    assert settings["env"] == {
        "KEEP": "yes",
        "LATER": "preserved",
        "ANTHROPIC_BASE_URL": BASE_URL,
        "ANTHROPIC_CUSTOM_HEADERS": f"Authorization: Bearer {KEY}",
    }
    assert read_json(backup_path())["version"] == 2
    assert not helper_path().exists()

    reset_integration()
    assert read_json(claude_settings_path()) == {
        **original,
        "verbose": True,
        "env": {
            "KEEP": "yes",
            "ANTHROPIC_API_KEY": "original-key",
            "LATER": "preserved",
        },
    }


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

    settings = read_json(claude_settings_path())
    assert settings["theme"] == "dark"
    assert settings["env"] == {
        "KEEP": "yes",
        "ANTHROPIC_BASE_URL": BASE_URL,
        "ANTHROPIC_CUSTOM_HEADERS": f"Authorization: Bearer {KEY}",
    }

    reset_integration()
    assert read_json(claude_settings_path()) == {"theme": "dark", "env": {"KEEP": "yes"}}


def test_reset_removes_tool_data_and_restores_native_settings(
    isolated_home, sample_models
) -> None:
    original = {"theme": "dark", "model": "sonnet"}
    write_json(claude_settings_path(), original)
    write_credential(KEY)
    configure_claude(sample_models[:1])

    assert reset_integration() is True
    assert read_json(claude_settings_path()) == original
    assert not config_dir().exists()


def test_reset_restores_unmigrated_legacy_integration(
    isolated_home, sample_models
) -> None:
    original = {"model": "sonnet"}
    write_json(claude_settings_path(), original)
    write_legacy_backup(original)
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
