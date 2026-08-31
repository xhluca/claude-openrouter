from __future__ import annotations

import io

from claude_openrouter import cli
from claude_openrouter.check import ToolProbeResult
from claude_openrouter.openrouter import save_catalog, write_credential
from claude_openrouter.paths import anthropic_credential_path, claude_settings_path
from claude_openrouter.settings import load_preferences, save_preferences

KEY = "sk-or-v1-this-is-a-fake-test-key"
ANTHROPIC_KEY = "sk-ant-this-is-a-fake-test-key"


class TtyBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_fetch_alias_parses() -> None:
    assert cli.parser().parse_args(["fetch"]).command == "fetch"


def test_update_and_upgrade_parse() -> None:
    assert cli.parser().parse_args(["update"]).command == "update"
    assert cli.parser().parse_args(["upgrade"]).command == "upgrade"


def test_claude_passes_through_arguments() -> None:
    args = cli.parser().parse_args(["claude"])
    assert args.command == "claude"
    assert args.claude_args == []


def test_update_dispatches_with_current_version(monkeypatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(cli, "update_installed_package", seen.append)

    assert cli.main(["update"]) == 0
    assert seen == [cli.__version__]


def test_masked_input_falls_back_for_non_terminal(monkeypatch) -> None:
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: f"{prompt}secret")
    assert cli._masked_input("Key: ") == "Key: secret"


def test_terminal_styles_use_color_and_respect_no_color(monkeypatch) -> None:
    stream = TtyBuffer()
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("NO_COLOR", raising=False)

    assert cli._styled("ready", "1;32", stream=stream) == "\x1b[1;32mready\x1b[0m"

    monkeypatch.setenv("NO_COLOR", "1")
    assert cli._styled("ready", "1;32", stream=stream) == "ready"


def test_existing_key_is_reused_after_confirmation(isolated_home, monkeypatch, capsys) -> None:
    write_credential(KEY)
    monkeypatch.setattr("builtins.input", lambda prompt: print(prompt) or "yes")
    monkeypatch.setattr(
        cli,
        "_masked_input",
        lambda _prompt: (_ for _ in ()).throw(AssertionError("must not prompt for a new key")),
    )

    assert cli._read_key(from_stdin=False) == KEY
    assert str(cli.credential_path()) in capsys.readouterr().out


def test_existing_key_can_be_replaced(isolated_home, monkeypatch) -> None:
    write_credential(KEY)
    replacement = "sk-or-v1-this-is-a-new-test-key"
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")
    monkeypatch.setattr(cli, "_masked_input", lambda prompt: replacement if prompt else "")

    assert cli._read_key(from_stdin=False) == replacement


def test_key_stdin_does_not_offer_reuse(isolated_home, monkeypatch) -> None:
    write_credential(KEY)
    replacement = "sk-or-v1-this-came-from-stdin"
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(f"{replacement}\n"))
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: (_ for _ in ()).throw(AssertionError("must not ask interactively")),
    )

    assert cli._read_key(from_stdin=True) == replacement


def test_search_always_refreshes(sample_models, monkeypatch, capsys) -> None:
    called = 0

    def refresh():
        nonlocal called
        called += 1
        return sample_models

    monkeypatch.setattr(cli, "refresh_catalog", refresh)
    assert cli.main(["search", "claude"]) == 0
    assert called == 1
    output = capsys.readouterr()
    assert "anthropic/claude-sonnet-4.6" in output.out
    assert "Refreshed 4 models; 2 matched." in output.err


def test_search_tools_filters_to_advertised_tool_models(
    sample_models, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(cli, "refresh_catalog", lambda: sample_models)

    assert cli.main(["search", "gemini", "--tools"]) == 0
    output = capsys.readouterr()
    assert "google/gemini-3.1-pro-preview" in output.out
    assert "qwen/qwen3-coder" not in output.out
    assert "1 tool-capable matched" in output.err


def test_check_runs_a_live_probe_without_requiring_a_favorite(
    sample_models, monkeypatch, capsys
) -> None:
    checked = []
    monkeypatch.setattr(cli, "refresh_catalog", lambda: sample_models)
    monkeypatch.setattr(
        cli,
        "probe_model",
        lambda model: checked.append(model)
        or ToolProbeResult(
            tool_called=True,
            tool_completed=True,
            acknowledged_result=True,
            returncode=0,
            total_cost_usd=0.00125,
            final_text="CLOR_TOOL_CHECK_OK",
            diagnostic="",
        ),
    )

    assert (
        cli.main(
            ["check", "clor/openrouter/google/gemini-3.1-pro-preview", "--yes"]
        )
        == 0
    )
    assert checked == [sample_models[2]]
    output = capsys.readouterr().out
    assert "Tool round-trip passed" in output
    assert "$0.001250 reported cost" in output


def test_check_explains_a_model_that_does_not_call_tools(
    sample_models, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(cli, "refresh_catalog", lambda: sample_models)
    monkeypatch.setattr(
        cli,
        "probe_model",
        lambda _model: ToolProbeResult(
            tool_called=False,
            tool_completed=False,
            acknowledged_result=False,
            returncode=0,
            total_cost_usd=None,
            final_text="I cannot do that.",
            diagnostic="",
        ),
    )

    assert cli.main(["check", "qwen/qwen3-coder", "--yes"]) == 1
    assert "no Glob tool call was emitted" in capsys.readouterr().err


def test_check_defaults_to_no_before_sending_a_billable_request(
    isolated_home, sample_models, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(cli, "refresh_catalog", lambda: sample_models)
    monkeypatch.setattr(cli.sys, "stdin", TtyBuffer())
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    monkeypatch.setattr(
        cli,
        "probe_model",
        lambda _model: (_ for _ in ()).throw(AssertionError("must not send a request")),
    )

    assert cli.main(["check", "google/gemini-3.1-pro-preview"]) == 0
    output = capsys.readouterr().out
    assert "Estimated charge: about $0.036–$1.024" in output
    assert "Cancelled; no billable request was sent" in output


def test_check_requires_explicit_yes_outside_a_terminal(
    isolated_home, sample_models, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(cli, "refresh_catalog", lambda: sample_models)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
    monkeypatch.setattr(
        cli,
        "probe_model",
        lambda _model: (_ for _ in ()).throw(AssertionError("must not send a request")),
    )

    assert cli.main(["check", "google/gemini-3.1-pro-preview"]) == 1
    assert "rerun with --yes" in capsys.readouterr().err


def test_check_always_allow_is_persistent_and_reversible(
    isolated_home, sample_models, monkeypatch, capsys
) -> None:
    checked = []
    result = ToolProbeResult(
        tool_called=True,
        tool_completed=True,
        acknowledged_result=True,
        returncode=0,
        total_cost_usd=0.001,
        final_text="CLOR_TOOL_CHECK_OK",
        diagnostic="",
    )
    monkeypatch.setattr(cli, "refresh_catalog", lambda: sample_models)
    monkeypatch.setattr(cli.sys, "stdin", TtyBuffer())
    monkeypatch.setattr("builtins.input", lambda _prompt: "always")
    monkeypatch.setattr(
        cli, "probe_model", lambda model: checked.append(model["id"]) or result
    )

    assert cli.main(["check", "google/gemini-3.1-pro-preview"]) == 0
    assert load_preferences()["confirm_billable_checks"] is False
    assert "Future checks will not ask" in capsys.readouterr().out

    monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: (_ for _ in ()).throw(AssertionError("must not ask again")),
    )
    assert cli.main(["check", "google/gemini-3.1-pro-preview"]) == 0
    assert len(checked) == 2

    assert cli.main(["config", "--check-confirmation", "ask"]) == 0
    assert load_preferences()["confirm_billable_checks"] is True


def test_setup_from_stdin_writes_favorites(
    isolated_home, sample_models, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(cli, "validate_key", lambda _key: None)
    monkeypatch.setattr(cli, "refresh_catalog", lambda _key=None: sample_models)
    monkeypatch.setattr(cli, "_warn_claude_compatibility", lambda: None)
    monkeypatch.setattr(cli, "has_native_login", lambda: True)
    monkeypatch.setattr(cli, "start_service", lambda _port: "test service")
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(f"{KEY}\n"))

    assert (
        cli.main(
            [
                "setup",
                "--key-stdin",
                "--models",
                "google/gemini-3.1-pro-preview",
                "qwen/qwen3-coder",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "Favorites:" in output
    assert "qwen/qwen3-coder" in output


def test_select_positional_maps_to_one_model(
    isolated_home, sample_models, monkeypatch, capsys
) -> None:
    write_credential(KEY)
    monkeypatch.setattr(cli, "refresh_catalog", lambda _key=None: sample_models)
    monkeypatch.setattr(cli, "has_native_login", lambda: True)
    monkeypatch.setattr(cli, "start_service", lambda _port: "test service")
    assert cli.main(["select", "google/gemini-3.1-pro-preview"]) == 0
    output = capsys.readouterr().out
    assert "Saved 1 /model favorite" in output
    assert "run /agents" in output


def test_select_warns_when_tools_are_not_advertised(
    isolated_home, sample_models, monkeypatch, capsys
) -> None:
    write_credential(KEY)
    monkeypatch.setattr(cli, "refresh_catalog", lambda _key=None: sample_models)
    monkeypatch.setattr(cli, "has_native_login", lambda: True)
    monkeypatch.setattr(cli, "start_service", lambda _port: "test service")

    assert cli.main(["select", "qwen/qwen3-coder"]) == 0
    error = capsys.readouterr().err
    assert "do not advertise OpenRouter tool calling" in error
    assert "clor check qwen/qwen3-coder" in error


def test_select_rejects_ambiguous_arguments(isolated_home, sample_models, monkeypatch) -> None:
    write_credential(KEY)
    monkeypatch.setattr(cli, "refresh_catalog", lambda _key=None: sample_models)
    assert cli.main(["select", "qwen/qwen3-coder", "--model", "other/model"]) == 1


def test_claude_prepares_favorites_then_launches(
    isolated_home, sample_models, monkeypatch
) -> None:
    write_credential(KEY)
    monkeypatch.setattr(cli, "favorite_ids", lambda: ["qwen/qwen3-coder"])
    monkeypatch.setattr(cli, "load_catalog", lambda: sample_models)
    monkeypatch.setattr(cli, "has_native_login", lambda: True)
    monkeypatch.setattr(cli, "healthcheck", lambda _port: True)
    launched = []
    monkeypatch.setattr(cli, "launch_claude", launched.append)

    assert cli.main(["claude", "--continue"]) == 0
    assert launched == [["--continue"]]
    assert "qwen/qwen3-coder" in claude_settings_path().read_text()


def test_config_can_switch_native_models_to_private_anthropic_api_key(
    isolated_home, sample_models, monkeypatch
) -> None:
    write_credential(KEY)
    save_catalog(sample_models)
    save_preferences(sample_models[2:3], sample_models[2]["id"])
    monkeypatch.setattr(cli, "has_native_login", lambda: True)
    monkeypatch.setattr(cli, "start_service", lambda _port: "test service")
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(f"{ANTHROPIC_KEY}\n"))

    assert cli.main(["config", "--anthropic-key-stdin"]) == 0
    assert anthropic_credential_path().read_text().strip() == ANTHROPIC_KEY
    assert load_preferences()["anthropic_auth"] == "api"


def test_update_restarts_an_existing_hybrid_router(
    isolated_home, sample_models, monkeypatch
) -> None:
    save_preferences(sample_models[2:3], sample_models[2]["id"])
    updated: list[str] = []
    restarted: list[int] = []
    monkeypatch.setattr(cli, "update_installed_package", updated.append)
    monkeypatch.setattr(cli, "start_service", lambda port: restarted.append(port) or "test")

    assert cli.main(["update"]) == 0
    assert updated == [cli.__version__]
    assert restarted == [9417]
