"""Command-line interface for Claude OpenRouter."""

from __future__ import annotations

import argparse
import getpass
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .launcher import launch_claude
from .models import exact_models, print_models, search_models
from .openrouter import (
    load_catalog,
    read_credential,
    refresh_catalog,
    validate_key,
    validate_key_shape,
    write_credential,
)
from .paths import catalog_path, claude_settings_path, credential_path
from .picker import choose_models
from .settings import (
    assert_private_files,
    configure_claude,
    favorite_ids,
    reset_integration,
)
from .uninstall import remove_installed_package
from .update import update_installed_package

MINIMUM_CLAUDE_VERSION = (2, 1, 242)


def _supports_color(stream: Any) -> bool:
    return (
        "NO_COLOR" not in os.environ
        and os.environ.get("TERM") != "dumb"
        and bool(getattr(stream, "isatty", lambda: False)())
    )


def _styled(value: object, code: str, *, stream: Any | None = None) -> str:
    target = sys.stdout if stream is None else stream
    text = str(value)
    return f"\x1b[{code}m{text}\x1b[0m" if _supports_color(target) else text


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="claude-openrouter",
        description="Connect Claude Code directly to selected OpenRouter models.",
    )
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)

    index = commands.add_parser(
        "index",
        aliases=["fetch"],
        help="fetch and cache the current OpenRouter model catalog",
    )
    index.add_argument("--json", action="store_true", help="print the catalog as JSON")

    search = commands.add_parser("search", help="refresh and search the model catalog")
    search.add_argument("queries", nargs="+", help="glob patterns or plain terms (OR matching)")
    search.add_argument("--regex", action="store_true", help="interpret each query as a regex")
    search.add_argument("--json", action="store_true", help="print matches as JSON")

    setup = commands.add_parser("setup", help="configure the key and Claude Code again")
    setup.add_argument("--key-stdin", action="store_true", help="read the key from stdin")
    setup.add_argument("--no-validate", action="store_true", help="skip the key metadata check")
    setup.add_argument(
        "--models",
        nargs="+",
        metavar="MODEL",
        help="skip the picker and select these exact model IDs",
    )

    select = commands.add_parser("select", help="replace the saved /model favorites")
    select.add_argument("model", nargs="?", help="exact model ID (same as --model)")
    choice = select.add_mutually_exclusive_group()
    choice.add_argument("--model", dest="model_option", help="select one exact model ID")
    choice.add_argument("--models", nargs="+", metavar="MODEL", help="select exact model IDs")

    config = commands.add_parser("config", help="change the stored OpenRouter API key")
    config.add_argument("--key-stdin", action="store_true", help="read the key from stdin")
    config.add_argument("--no-validate", action="store_true", help="skip the key metadata check")

    commands.add_parser("reset", help="restore Claude Code's original settings and remove data")
    commands.add_parser("uninstall", help="reset the integration and uninstall this tool")
    commands.add_parser(
        "update",
        aliases=["upgrade"],
        help="install the latest Claude OpenRouter release",
    )
    claude = commands.add_parser(
        "claude",
        help="launch Claude Code with OpenRouter favorites and native connectors",
    )
    claude.add_argument(
        "claude_args",
        nargs=argparse.REMAINDER,
        help="arguments passed through to Claude Code",
    )
    return root


def _existing_key() -> str | None:
    try:
        return read_credential()
    except RuntimeError:
        return None


def _masked_input(prompt: str) -> str:
    """Read a secret from a terminal while showing one mask per character."""
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        return getpass.getpass(prompt)

    try:
        import termios
    except ImportError:
        return getpass.getpass(prompt)

    descriptor = sys.stdin.fileno()
    original = termios.tcgetattr(descriptor)
    masked = original.copy()
    masked[6] = original[6].copy()
    masked[3] &= ~(termios.ECHO | termios.ICANON)
    masked[6][termios.VMIN] = 1
    masked[6][termios.VTIME] = 0
    characters: list[str] = []
    sys.stderr.write(prompt)
    sys.stderr.flush()
    try:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, masked)
        while True:
            character = sys.stdin.read(1)
            if character in {"\n", "\r"}:
                sys.stderr.write("\n")
                sys.stderr.flush()
                return "".join(characters)
            if character in {"\b", "\x7f"}:
                if characters:
                    characters.pop()
                    sys.stderr.write("\b \b")
                    sys.stderr.flush()
                continue
            if character == "\x04":
                if not characters:
                    raise EOFError
                continue
            if character.isprintable():
                characters.append(character)
                sys.stderr.write("*")
                sys.stderr.flush()
    except BaseException:
        sys.stderr.write("\n")
        sys.stderr.flush()
        raise
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, original)


def _confirm_key_reuse(path: Path) -> bool:
    while True:
        answer = input(f"Reuse the OpenRouter API key at {path}? [Y/n]: ").strip().casefold()
        if answer in {"", "y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Enter y or n.", file=sys.stderr)


def _read_key(*, from_stdin: bool) -> str:
    if from_stdin:
        key = sys.stdin.readline().strip()
    else:
        existing = _existing_key()
        if existing and _confirm_key_reuse(credential_path()):
            return existing
        key = _masked_input("OpenRouter API key: ").strip()
    validate_key_shape(key)
    return key


def _claude_version() -> tuple[int, ...] | None:
    executable = shutil.which("claude")
    if not executable:
        candidate = Path.home() / ".local" / "bin" / "claude"
        executable = str(candidate) if candidate.is_file() else None
    if not executable:
        return None
    result = subprocess.run(
        [executable, "--version"], check=False, capture_output=True, text=True, timeout=10
    )
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", result.stdout)
    return tuple(int(part) for part in match.groups()) if match else None


def _warn_claude_compatibility() -> None:
    version = _claude_version()
    required = ".".join(str(part) for part in MINIMUM_CLAUDE_VERSION)
    if version is None:
        print(
            f"warning: Claude Code was not found; install version {required}+ before use",
            file=sys.stderr,
        )
    elif version < MINIMUM_CLAUDE_VERSION:
        actual = ".".join(str(part) for part in version)
        print(
            f"warning: Claude Code {actual} is too old for multi-model /model favorites; "
            f"upgrade to {required}+",
            file=sys.stderr,
        )


def _choose_or_validate(
    models: list[dict[str, Any]], requested: list[str] | None
) -> list[dict[str, Any]]:
    if requested is not None:
        return exact_models(models, requested)
    chosen = choose_models(models, favorite_ids())
    if chosen is None:
        raise RuntimeError("selection cancelled")
    return exact_models(models, chosen)


def command_index(*, as_json: bool) -> int:
    models = refresh_catalog()
    print_models(models, as_json=as_json)
    if not as_json:
        print(f"\nIndexed {len(models)} models in {catalog_path()}.", file=sys.stderr)
    return 0


def command_search(queries: list[str], *, regex: bool, as_json: bool) -> int:
    models = refresh_catalog()
    matches = search_models(models, queries, regex=regex)
    print_models(matches, as_json=as_json)
    print(
        f"Refreshed {len(models)} models; {len(matches)} matched.",
        file=sys.stderr,
    )
    return 0 if matches else 1


def command_setup(*, key_stdin: bool, no_validate: bool, ids: list[str] | None) -> int:
    key = _read_key(from_stdin=key_stdin)
    if not no_validate:
        validate_key(key)
    models = refresh_catalog(key)
    selected = _choose_or_validate(models, ids)
    write_credential(key)
    settings = configure_claude(selected)
    assert_private_files()
    _warn_claude_compatibility()
    print(_styled("✓ Claude OpenRouter is ready", "1;32"))
    print()
    print(
        f"{_styled('OpenRouter credential:', '1;36')} "
        f"{_styled(credential_path(), '36')} {_styled('(mode 0600)', '2')}"
    )
    print(
        f"{_styled('Model index:', '1;36')} "
        f"{_styled(catalog_path(), '36')} {_styled(f'({len(models)} models)', '2')}"
    )
    print(f"{_styled('OpenRouter launch settings:', '1;36')} {_styled(settings, '36')}")
    print()
    print(_styled("Favorites:", "1;35"))
    for model in selected:
        print(f"  {_styled('●', '1;32')} {_styled(model['id'], '1;36')}")
    print()
    print(
        f"{_styled('Next:', '1;33')} run {_styled('clor claude', '1;32')}, then use "
        f"{_styled('/model', '1;35')} to switch OpenRouter favorites."
    )
    return 0


def command_select(
    positional: str | None, model_option: str | None, models_option: list[str] | None
) -> int:
    if positional and (model_option or models_option):
        raise ValueError("use a positional model, --model, or --models, not more than one")
    requested: list[str] | None
    if positional:
        requested = [positional]
    elif model_option:
        requested = [model_option]
    else:
        requested = models_option
    models = refresh_catalog()
    selected = _choose_or_validate(models, requested)
    settings = configure_claude(selected)
    assert_private_files()
    print(
        _styled(
            f"✓ Saved {len(selected)} /model favorite(s) to {settings}:",
            "1;32",
        )
    )
    for model in selected:
        print(_styled(f"  - {model['id']}", "1;36"))
    return 0


def command_config(*, key_stdin: bool, no_validate: bool) -> int:
    key = _read_key(from_stdin=key_stdin)
    if not no_validate:
        validate_key(key)
    models = refresh_catalog(key)
    write_credential(key)
    print(f"OpenRouter credential updated: {credential_path()} (mode 0600)")
    print(f"Model index refreshed: {len(models)} models")
    return 0


def command_claude(arguments: list[str]) -> int:
    ids = favorite_ids()
    if not ids:
        raise RuntimeError("no OpenRouter favorites are configured; run setup")
    configure_claude(exact_models(load_catalog(), ids))
    assert_private_files()
    launch_claude(arguments)
    return 0


def command_reset() -> int:
    restored = reset_integration()
    if restored:
        print(f"Restored Claude Code settings at {claude_settings_path()}.")
    else:
        print("Claude Code settings did not need restoring.")
    print("Removed the OpenRouter credential, favorites, index, and integration state.")
    return 0


def command_uninstall() -> int:
    command_reset()
    if remove_installed_package():
        print("Uninstalled claude-openrouter.")
    else:
        print(
            "Integration reset, but this install is not managed by uv or the curl installer. "
            "Remove claude-openrouter with the package manager that installed it.",
            file=sys.stderr,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    if raw_arguments[:1] == ["claude"]:
        args = parser().parse_args(["claude"])
        args.claude_args = raw_arguments[1:]
    else:
        args = parser().parse_args(raw_arguments)
    try:
        if args.command in {"index", "fetch"}:
            return command_index(as_json=args.json)
        if args.command == "search":
            return command_search(args.queries, regex=args.regex, as_json=args.json)
        if args.command == "setup":
            return command_setup(
                key_stdin=args.key_stdin,
                no_validate=args.no_validate,
                ids=args.models,
            )
        if args.command == "select":
            return command_select(args.model, args.model_option, args.models)
        if args.command == "config":
            return command_config(key_stdin=args.key_stdin, no_validate=args.no_validate)
        if args.command == "reset":
            return command_reset()
        if args.command == "uninstall":
            return command_uninstall()
        if args.command in {"update", "upgrade"}:
            update_installed_package(__version__)
            return 0
        if args.command == "claude":
            return command_claude(args.claude_args)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2
