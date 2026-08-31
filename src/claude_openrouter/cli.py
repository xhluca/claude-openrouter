"""Command-line interface for Claude OpenRouter."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .agents import run_agent_hook
from .anthropic import (
    read_anthropic_credential,
    validate_anthropic_key_shape,
    write_anthropic_credential,
)
from .check import (
    PROBE_INPUT_TOKEN_ESTIMATE,
    PROBE_INPUT_TOKEN_PLANNING_MAX,
    PROBE_OUTPUT_TOKEN_ESTIMATE,
    PROBE_OUTPUT_TOKEN_PLANNING_MAX,
    estimate_probe_cost,
    probe_model,
)
from .launcher import has_native_login, launch_claude
from .models import (
    exact_models,
    hybrid_openrouter_allowed,
    original_model,
    print_models,
    search_models,
    supports_tools,
    tool_capability_badge,
)
from .openrouter import (
    load_catalog,
    read_credential,
    refresh_catalog,
    validate_key,
    validate_key_shape,
    write_credential,
)
from .paths import anthropic_credential_path, catalog_path, claude_settings_path, credential_path
from .picker import choose_models
from .proxy import DEFAULT_HOST, DEFAULT_PORT, run_router
from .service import healthcheck, start_service, stop_service
from .settings import (
    assert_private_files,
    configure_claude,
    favorite_ids,
    load_preferences,
    refresh_claude_credential,
    reset_integration,
    set_check_confirmation,
)
from .storage import read_json_object
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
        description="Route native Claude and selected OpenRouter models safely.",
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
    search.add_argument(
        "--tools",
        action="store_true",
        help="show only models that advertise tool calling",
    )
    search.add_argument("--json", action="store_true", help="print matches as JSON")

    check = commands.add_parser(
        "check",
        help="run a billable Claude Code tool round-trip for one model",
    )
    check.add_argument("model", help="exact OpenRouter model ID (need not be a favorite)")
    check.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="run without the interactive billing confirmation",
    )

    setup = commands.add_parser("setup", help="configure the key and Claude Code again")
    setup.add_argument("--key-stdin", action="store_true", help="read the key from stdin")
    setup.add_argument("--no-validate", action="store_true", help="skip the key metadata check")
    setup.add_argument(
        "--models",
        nargs="+",
        metavar="MODEL",
        help="skip the picker and select these exact model IDs",
    )
    setup.add_argument(
        "--anthropic-key-stdin",
        action="store_true",
        help="read an Anthropic API key from stdin after the OpenRouter key",
    )
    setup.add_argument(
        "--anthropic-auth",
        choices=("max", "api"),
        default="max",
        help="route native Claude models through Max OAuth or an Anthropic API key",
    )
    setup.add_argument("--port", type=int, default=DEFAULT_PORT, help=argparse.SUPPRESS)

    select = commands.add_parser("select", help="replace the saved /model favorites")
    select.add_argument("model", nargs="?", help="exact model ID (same as --model)")
    choice = select.add_mutually_exclusive_group()
    choice.add_argument("--model", dest="model_option", help="select one exact model ID")
    choice.add_argument("--models", nargs="+", metavar="MODEL", help="select exact model IDs")

    config = commands.add_parser("config", help="change credentials and CLI preferences")
    config.add_argument("--key-stdin", action="store_true", help="read the key from stdin")
    config.add_argument("--no-validate", action="store_true", help="skip the key metadata check")
    config.add_argument(
        "--anthropic-auth",
        choices=("max", "api"),
        help="change how native Claude models are billed",
    )
    config.add_argument(
        "--anthropic-key-stdin",
        action="store_true",
        help="read and store an Anthropic API key, then select API billing",
    )
    config.add_argument(
        "--check-confirmation",
        choices=("ask", "never"),
        help="ask before billable model checks, or never ask",
    )

    doctor = commands.add_parser("doctor", help="check hybrid routing and Claude authentication")
    doctor.add_argument("--json", action="store_true", help="print machine-readable status")

    serve = commands.add_parser("serve", help="run the loopback hybrid router")
    serve.add_argument("--host", default=DEFAULT_HOST)
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)

    commands.add_parser("reset", help="restore Claude Code's original settings and remove data")
    commands.add_parser("uninstall", help="reset the integration and uninstall this tool")
    commands.add_parser(
        "update",
        aliases=["upgrade"],
        help="install the latest Claude OpenRouter release",
    )
    claude = commands.add_parser(
        "claude",
        help="compatibility alias for the ordinary claude command",
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


def _confirm_key_reuse(path: Path, label: str = "OpenRouter API key") -> bool:
    while True:
        answer = input(f"Reuse the {label} at {path}? [Y/n]: ").strip().casefold()
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


def _read_anthropic_key(*, from_stdin: bool) -> str:
    if from_stdin:
        key = sys.stdin.readline().strip()
    else:
        try:
            existing = read_anthropic_credential()
        except RuntimeError:
            existing = None
        if existing and _confirm_key_reuse(anthropic_credential_path(), "Anthropic API key"):
            return existing
        key = _masked_input("Anthropic API key: ").strip()
    validate_anthropic_key_shape(key)
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
    eligible = [model for model in models if hybrid_openrouter_allowed(str(model.get("id", "")))]
    chosen = choose_models(eligible, favorite_ids())
    if chosen is None:
        raise RuntimeError("selection cancelled")
    return exact_models(eligible, chosen)


def _warn_selected_tool_support(models: list[dict[str, Any]]) -> None:
    unsupported = [str(model["id"]) for model in models if not supports_tools(model)]
    if not unsupported:
        return
    print(
        _styled(
            "warning: these favorites do not advertise OpenRouter tool calling: "
            f"{', '.join(unsupported)}",
            "1;33",
            stream=sys.stderr,
        ),
        file=sys.stderr,
    )
    print(
        "They may still chat, but Claude Code agent actions can fail. "
        f"Run `clor check {unsupported[0]}` for a live tool round-trip.",
        file=sys.stderr,
    )


def command_index(*, as_json: bool) -> int:
    models = refresh_catalog()
    print_models(models, as_json=as_json)
    if not as_json:
        print(f"\nIndexed {len(models)} models in {catalog_path()}.", file=sys.stderr)
    return 0


def command_search(
    queries: list[str], *, regex: bool, tools_only: bool, as_json: bool
) -> int:
    models = refresh_catalog()
    matches = search_models(models, queries, regex=regex)
    if tools_only:
        matches = [model for model in matches if supports_tools(model)]
    print_models(matches, as_json=as_json)
    qualifier = " tool-capable" if tools_only else ""
    print(
        f"Refreshed {len(models)} models; {len(matches)}{qualifier} matched.",
        file=sys.stderr,
    )
    return 0 if matches else 1


def _formatted_probe_estimate(model: dict[str, Any]) -> str:
    low = estimate_probe_cost(model)
    high = estimate_probe_cost(
        model,
        input_tokens=PROBE_INPUT_TOKEN_PLANNING_MAX,
        output_tokens=PROBE_OUTPUT_TOKEN_PLANNING_MAX,
    )
    if low is None or high is None:
        return "Estimated charge: unavailable from this model's catalog pricing."
    return (
        f"Estimated charge: about ${low:.3f}–${high:.3f} at current catalog rates "
        f"({PROBE_INPUT_TOKEN_ESTIMATE // 1000}K–"
        f"{PROBE_INPUT_TOKEN_PLANNING_MAX // 1000}K input + "
        f"{PROBE_OUTPUT_TOKEN_ESTIMATE:,}–{PROBE_OUTPUT_TOKEN_PLANNING_MAX:,} output tokens)."
    )


def _confirm_billable_check(model: dict[str, Any], *, assume_yes: bool) -> bool:
    print(_formatted_probe_estimate(model))
    print("Actual usage and provider charges may vary.")
    confirmation_required = load_preferences().get("confirm_billable_checks", True)
    if assume_yes or confirmation_required is False:
        return True
    if not sys.stdin.isatty():
        raise RuntimeError(
            "billable model checks require confirmation in a terminal; rerun with --yes"
        )
    while True:
        answer = input("Continue? [y] Yes / [N] No / [a] Always allow: ").strip().casefold()
        if answer in {"y", "yes"}:
            return True
        if answer in {"a", "always"}:
            set_check_confirmation(False)
            print(
                "Future checks will not ask. Restore prompts with "
                "`clor config --check-confirmation ask`."
            )
            return True
        if answer in {"", "n", "no"}:
            print("Cancelled; no billable request was sent.")
            return False
        print("Enter y, n, or a.", file=sys.stderr)


def command_check(requested_model: str, *, assume_yes: bool) -> int:
    model_id = original_model(requested_model) or requested_model
    if not hybrid_openrouter_allowed(model_id):
        raise ValueError(
            "tool checks are for non-Anthropic OpenRouter models; use Claude Code "
            "directly for built-in Claude models"
        )
    model = exact_models(refresh_catalog(), [model_id])[0]
    print(f"Model: {model_id}")
    print(f"Catalog: {tool_capability_badge(model, detailed=True)}")
    if not _confirm_billable_check(model, assume_yes=assume_yes):
        return 0
    print("Running an isolated, billable Claude Code Glob round-trip…")
    result = probe_model(model)
    if result.passed:
        cost = (
            f" · ${result.total_cost_usd:.6f} reported cost"
            if result.total_cost_usd is not None
            else ""
        )
        print(_styled(f"✓ Tool round-trip passed{cost}", "1;32"))
        print("The model emitted a valid Glob call, consumed its result, and continued.")
        return 0

    failures: list[str] = []
    if result.returncode != 0:
        failures.append(f"Claude Code exited with status {result.returncode}")
    if not result.tool_called:
        failures.append("no Glob tool call was emitted")
    elif not result.tool_completed:
        failures.append("the Glob tool result did not complete successfully")
    if not result.acknowledged_result:
        failures.append("the model did not continue correctly after the tool result")
    print(_styled("✗ Tool round-trip failed", "1;31"), file=sys.stderr)
    for failure in failures:
        print(f"  - {failure}", file=sys.stderr)
    if result.diagnostic:
        first_line = result.diagnostic.splitlines()[0]
        print(f"  - Claude Code: {first_line[:300]}", file=sys.stderr)
    return 1


def command_setup(
    *,
    key_stdin: bool,
    no_validate: bool,
    ids: list[str] | None,
    anthropic_auth: str,
    anthropic_key_stdin: bool,
    port: int,
) -> int:
    key = _read_key(from_stdin=key_stdin)
    if not no_validate:
        validate_key(key)
    models = refresh_catalog(key)
    selected = _choose_or_validate(models, ids)
    _warn_selected_tool_support(selected)
    write_credential(key)
    if anthropic_auth == "api":
        write_anthropic_credential(_read_anthropic_key(from_stdin=anthropic_key_stdin))
    native_login = has_native_login()
    if anthropic_auth == "max" and not native_login:
        raise RuntimeError(
            "Claude Max routing requires a native first-party Claude login. Run "
            "`claude auth status --json`; if it reports loggedIn=false, run "
            "`claude auth login`, then retry setup"
        )
    settings = configure_claude(
        selected,
        native_login=native_login,
        anthropic_auth=anthropic_auth,
        port=port,
    )
    service = start_service(port)
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
    print(f"{_styled('Claude Code settings:', '1;36')} {_styled(settings, '36')}")
    print(
        f"{_styled('Hybrid router:', '1;36')} "
        f"{_styled(f'http://127.0.0.1:{port}', '36')} {_styled(f'({service})', '2')}"
    )
    print(
        f"{_styled('Native Claude billing:', '1;36')} "
        f"{_styled('Max subscription' if anthropic_auth == 'max' else 'Anthropic API', '1;35')}"
    )
    print()
    print(_styled("Favorites:", "1;35"))
    for model in selected:
        badge_color = "1;32" if supports_tools(model) else "1;33"
        print(
            f"  {_styled('●', '1;32')} {_styled(model['id'], '1;36')} "
            f"{_styled(f'[{tool_capability_badge(model)}]', badge_color)}"
        )
    print()
    print(
        f"{_styled('Next:', '1;33')} run {_styled('claude', '1;32')}, then use "
        f"{_styled('/model', '1;35')} to switch between native and OpenRouter models."
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
    _warn_selected_tool_support(selected)
    preferences = load_preferences()
    port = preferences.get("router_port", DEFAULT_PORT)
    auth = preferences.get("anthropic_auth", "max")
    if not isinstance(port, int) or not isinstance(auth, str):
        raise RuntimeError("invalid hybrid router preferences")
    settings = configure_claude(
        selected,
        native_login=has_native_login(),
        anthropic_auth=auth,
        port=port,
    )
    start_service(port)
    assert_private_files()
    print(
        _styled(
            f"✓ Saved {len(selected)} /model favorite(s) to {settings}:",
            "1;32",
        )
    )
    for model in selected:
        print(
            _styled(f"  - {model['id']}", "1;36"),
            _styled(
                f"[{tool_capability_badge(model)}]",
                "1;32" if supports_tools(model) else "1;33",
            ),
        )
    print("Existing Claude sessions: run /agents to reload favorites without restarting.")
    return 0


def command_config(
    *,
    key_stdin: bool,
    no_validate: bool,
    anthropic_auth: str | None,
    anthropic_key_stdin: bool,
    check_confirmation: str | None,
) -> int:
    if check_confirmation is not None:
        if key_stdin or no_validate or anthropic_auth is not None or anthropic_key_stdin:
            raise ValueError(
                "configure billing-check confirmation separately from credentials"
            )
        required = check_confirmation == "ask"
        set_check_confirmation(required)
        state = "required" if required else "disabled"
        print(f"Billable model-check confirmation is now {state}.")
        return 0
    if key_stdin and (anthropic_auth is not None or anthropic_key_stdin):
        raise ValueError("configure OpenRouter and Anthropic credentials in separate commands")
    if anthropic_key_stdin and anthropic_auth == "max":
        raise ValueError("an Anthropic API key cannot be combined with Max billing")
    if anthropic_auth is not None or anthropic_key_stdin:
        auth = "api" if anthropic_key_stdin else anthropic_auth
        assert auth is not None
        if auth == "api":
            write_anthropic_credential(
                _read_anthropic_key(from_stdin=anthropic_key_stdin)
            )
        preferences = load_preferences()
        ids = favorite_ids()
        if not ids:
            raise RuntimeError("no favorites are configured; run setup first")
        port = preferences.get("router_port", DEFAULT_PORT)
        if not isinstance(port, int):
            raise RuntimeError("invalid hybrid router port")
        settings = configure_claude(
            exact_models(load_catalog(), ids),
            native_login=has_native_login(),
            anthropic_auth=auth,
            port=port,
        )
        service = start_service(port)
        print(f"Native Claude billing changed to {auth}: {settings}")
        print(f"Hybrid router restarted via {service}.")
        return 0
    key = _read_key(from_stdin=key_stdin)
    if not no_validate:
        validate_key(key)
    models = refresh_catalog(key)
    write_credential(key)
    refresh_claude_credential(key)
    assert_private_files()
    print(f"OpenRouter credential updated: {credential_path()} (mode 0600)")
    print(f"Model index refreshed: {len(models)} models")
    return 0


def command_claude(arguments: list[str]) -> int:
    ids = favorite_ids()
    if not ids:
        raise RuntimeError("no OpenRouter favorites are configured; run setup")
    preferences = load_preferences()
    port = preferences.get("router_port", DEFAULT_PORT)
    auth = preferences.get("anthropic_auth", "max")
    if not isinstance(port, int) or not isinstance(auth, str):
        raise RuntimeError("invalid hybrid router preferences")
    configure_claude(
        exact_models(load_catalog(), ids),
        native_login=has_native_login(),
        anthropic_auth=auth,
        port=port,
    )
    if not healthcheck(port):
        start_service(port)
    assert_private_files()
    launch_claude(arguments)
    return 0


def command_reset() -> int:
    stop_service()
    restored = reset_integration()
    if restored:
        print(f"Restored Claude Code settings at {claude_settings_path()}.")
    else:
        print("Claude Code settings did not need restoring.")
    print("Removed the OpenRouter credential, favorites, index, and integration state.")
    return 0


def command_doctor(*, as_json: bool) -> int:
    preferences = load_preferences()
    port = preferences.get("router_port", DEFAULT_PORT)
    auth = preferences.get("anthropic_auth", "max")
    native_login = has_native_login()
    try:
        read_credential()
        openrouter_credential = True
    except RuntimeError:
        openrouter_credential = False
    try:
        read_anthropic_credential()
        anthropic_credential = True
    except RuntimeError:
        anthropic_credential = False
    settings = read_json_object(claude_settings_path(), missing_ok=True)
    env = settings.get("env")
    settings_active = isinstance(env, dict) and env.get("ANTHROPIC_BASE_URL") == (
        f"http://127.0.0.1:{port}"
    )
    status = {
        "configured": bool(favorite_ids()),
        "settings": settings_active,
        "router": healthcheck(port) if isinstance(port, int) else False,
        "router_url": f"http://127.0.0.1:{port}",
        "anthropic_auth": auth,
        "native_login": native_login,
        "openrouter_credential": openrouter_credential,
        "anthropic_credential": anthropic_credential if auth == "api" else None,
    }
    healthy = bool(
        status["configured"]
        and status["settings"]
        and status["router"]
        and status["openrouter_credential"]
    )
    if auth == "max":
        healthy = healthy and native_login
    elif auth == "api":
        healthy = healthy and anthropic_credential
    if as_json:
        print(json.dumps(status, indent=2))
    else:
        for label, value, ok in (
            ("Configuration", "ready" if status["configured"] else "missing", status["configured"]),
            ("Claude settings", "active" if settings_active else "inactive", settings_active),
            ("Hybrid router", "healthy" if status["router"] else "unavailable", status["router"]),
            (
                "Anthropic route",
                f"{auth} ({'logged in' if native_login else 'not logged in'})",
                native_login if auth == "max" else anthropic_credential,
            ),
        ):
            color = "1;32" if ok else "1;31"
            print(f"{_styled(label + ':', '1;36')} {_styled(value, color)}")
    return 0 if healthy else 1


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
    if raw_arguments == ["_agent-hook"]:
        return run_agent_hook()
    if raw_arguments[:1] == ["claude"]:
        args = parser().parse_args(["claude"])
        args.claude_args = raw_arguments[1:]
    else:
        args = parser().parse_args(raw_arguments)
    try:
        if args.command in {"index", "fetch"}:
            return command_index(as_json=args.json)
        if args.command == "search":
            return command_search(
                args.queries,
                regex=args.regex,
                tools_only=args.tools,
                as_json=args.json,
            )
        if args.command == "check":
            return command_check(args.model, assume_yes=args.yes)
        if args.command == "setup":
            return command_setup(
                key_stdin=args.key_stdin,
                no_validate=args.no_validate,
                ids=args.models,
                anthropic_auth=args.anthropic_auth,
                anthropic_key_stdin=args.anthropic_key_stdin,
                port=args.port,
            )
        if args.command == "select":
            return command_select(args.model, args.model_option, args.models)
        if args.command == "config":
            return command_config(
                key_stdin=args.key_stdin,
                no_validate=args.no_validate,
                anthropic_auth=args.anthropic_auth,
                anthropic_key_stdin=args.anthropic_key_stdin,
                check_confirmation=args.check_confirmation,
            )
        if args.command == "doctor":
            return command_doctor(as_json=args.json)
        if args.command == "serve":
            run_router(args.host, args.port)
            return 0
        if args.command == "reset":
            return command_reset()
        if args.command == "uninstall":
            return command_uninstall()
        if args.command in {"update", "upgrade"}:
            preferences = load_preferences()
            update_installed_package(__version__)
            if preferences.get("mode") == "hybrid":
                port = preferences.get("router_port", DEFAULT_PORT)
                if isinstance(port, int):
                    start_service(port)
            return 0
        if args.command == "claude":
            return command_claude(args.claude_args)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2
