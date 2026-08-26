from __future__ import annotations

import io

from claude_openrouter import cli
from claude_openrouter.openrouter import write_credential

KEY = "sk-or-v1-this-is-a-fake-test-key"


class TtyBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_fetch_alias_parses() -> None:
    assert cli.parser().parse_args(["fetch"]).command == "fetch"


def test_update_and_upgrade_parse() -> None:
    assert cli.parser().parse_args(["update"]).command == "update"
    assert cli.parser().parse_args(["upgrade"]).command == "upgrade"


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


def test_setup_from_stdin_writes_favorites(
    isolated_home, sample_models, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(cli, "validate_key", lambda _key: None)
    monkeypatch.setattr(cli, "refresh_catalog", lambda _key=None: sample_models)
    monkeypatch.setattr(cli, "_warn_claude_compatibility", lambda: None)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(f"{KEY}\n"))

    assert (
        cli.main(
            [
                "setup",
                "--key-stdin",
                "--models",
                "anthropic/claude-sonnet-4.6",
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
    assert cli.main(["select", "google/gemini-3.1-pro-preview"]) == 0
    assert "Saved 1 /model favorite" in capsys.readouterr().out


def test_select_rejects_ambiguous_arguments(isolated_home, sample_models, monkeypatch) -> None:
    write_credential(KEY)
    monkeypatch.setattr(cli, "refresh_catalog", lambda _key=None: sample_models)
    assert cli.main(["select", "qwen/qwen3-coder", "--model", "other/model"]) == 1
