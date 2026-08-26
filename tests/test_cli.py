from __future__ import annotations

import io

from claude_openrouter import cli
from claude_openrouter.openrouter import write_credential

KEY = "sk-or-v1-this-is-a-fake-test-key"


def test_fetch_alias_parses() -> None:
    assert cli.parser().parse_args(["fetch"]).command == "fetch"


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

