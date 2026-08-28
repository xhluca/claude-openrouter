from __future__ import annotations

import subprocess

import pytest

from claude_openrouter import update


def test_uv_tool_update_reinstalls_latest_unpinned_release(tmp_path, monkeypatch) -> None:
    tool_root = tmp_path / "uv-tools"
    monkeypatch.setattr(update.sys, "prefix", str(tool_root / update.PACKAGE_NAME))
    monkeypatch.setattr(update.shutil, "which", lambda name: "/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(update, "_command_stdout", lambda _command: str(tool_root))

    assert update._upgrade_command() == [
        "/bin/uv",
        "tool",
        "install",
        "--force",
        "--link-mode",
        "copy",
        "--refresh-package",
        update.PACKAGE_NAME,
        "--default-index",
        update.DEFAULT_INDEX_URL,
        update.PACKAGE_NAME,
    ]


def test_curl_venv_update_uses_its_own_python(tmp_path, monkeypatch) -> None:
    data_home = tmp_path / "data"
    tool_dir = data_home / update.PACKAGE_NAME / "tool"
    interpreter = tool_dir / "bin" / "python"
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setattr(update.sys, "prefix", str(tool_dir))
    monkeypatch.setattr(update.sys, "executable", str(interpreter))
    monkeypatch.setattr(update.shutil, "which", lambda _name: None)

    command = update._upgrade_command()

    assert command[:4] == [str(interpreter), "-m", "pip", "install"]
    assert "--upgrade" in command
    assert command[-1] == update.PACKAGE_NAME


def test_pipx_update_stays_with_pipx(tmp_path, monkeypatch) -> None:
    pipx_venvs = tmp_path / "pipx" / "venvs"
    monkeypatch.setattr(update.sys, "prefix", str(pipx_venvs / update.PACKAGE_NAME))
    monkeypatch.setattr(
        update.shutil,
        "which",
        lambda name: f"/bin/{name}" if name in {"uv", "pipx"} else None,
    )
    monkeypatch.setattr(
        update,
        "_command_stdout",
        lambda command: str(pipx_venvs) if command[0] == "/bin/pipx" else str(tmp_path / "uv"),
    )

    assert update._upgrade_command() == [
        "/bin/pipx",
        "upgrade",
        update.PACKAGE_NAME,
        "--index-url",
        update.DEFAULT_INDEX_URL,
    ]


def test_editable_venv_update_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(update.sys, "prefix", str(tmp_path / "venv"))
    monkeypatch.setattr(update.sys, "base_prefix", "/usr")
    monkeypatch.setattr(update.shutil, "which", lambda _name: None)
    monkeypatch.setattr(update, "_is_editable_install", lambda: True)

    with pytest.raises(RuntimeError, match="editable development install"):
        update._upgrade_command()


@pytest.mark.parametrize(
    ("installed", "expected"),
    [
        ("9.8.7", "Updated Claude OpenRouter from 1.2.3 to 9.8.7."),
        ("1.2.3", "Claude OpenRouter is already up to date at 1.2.3."),
    ],
)
def test_update_reports_before_and_after(installed, expected, monkeypatch, capsys) -> None:
    command = ["package-manager", "upgrade"]
    monkeypatch.setattr(update, "_source_install_location", lambda: None)
    monkeypatch.setattr(update, "_upgrade_command", lambda: command)
    monkeypatch.setattr(update, "_installed_version", lambda: installed)
    monkeypatch.setattr(
        update.subprocess,
        "run",
        lambda actual, check: subprocess.CompletedProcess(actual, 0)
        if actual == command and check is False
        else (_ for _ in ()).throw(AssertionError(actual)),
    )

    update.update_installed_package("1.2.3")

    output = capsys.readouterr().out
    assert "Checking for the latest Claude OpenRouter release…" in output
    assert output.rstrip().endswith(expected)


def test_update_keeps_a_source_install_that_is_ahead_of_pypi(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        update,
        "_source_install_location",
        lambda: "/work/claude-openrouter",
    )
    monkeypatch.setattr(update, "_published_version", lambda: "0.3.0")
    monkeypatch.setattr(
        update.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("the package manager must not run")
        ),
    )

    update.update_installed_package("0.4.0")

    assert "source build 0.4.0 is ahead of" in capsys.readouterr().out


def test_update_refuses_source_install_when_registry_comparison_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        update,
        "_source_install_location",
        lambda: "/work/claude-openrouter",
    )
    monkeypatch.setattr(update, "_published_version", lambda: None)

    with pytest.raises(RuntimeError, match="could not be compared safely"):
        update.update_installed_package("0.4.0")


def test_update_replaces_source_install_when_pypi_is_newer(monkeypatch, capsys) -> None:
    command = ["package-manager", "upgrade"]
    monkeypatch.setattr(update, "_source_install_location", lambda: "/work/source")
    monkeypatch.setattr(update, "_published_version", lambda: "0.5.0")
    monkeypatch.setattr(update, "_upgrade_command", lambda: command)
    monkeypatch.setattr(update, "_installed_version", lambda: "0.5.0")
    monkeypatch.setattr(
        update.subprocess,
        "run",
        lambda actual, check: subprocess.CompletedProcess(actual, 0)
        if actual == command and check is False
        else (_ for _ in ()).throw(AssertionError(actual)),
    )

    update.update_installed_package("0.4.0")

    assert "Updated Claude OpenRouter from 0.4.0 to 0.5.0." in capsys.readouterr().out
