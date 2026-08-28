"""Update the installed CLI with the package manager that owns it."""

from __future__ import annotations

import json
import os
import re
import shutil
import site
import subprocess
import sys
import urllib.error
import urllib.request
from importlib import metadata
from pathlib import Path
from urllib.parse import unquote, urlsplit

PACKAGE_NAME = "claude-openrouter"
DEFAULT_INDEX_URL = "https://pypi.org/simple"
PYPI_JSON_URL = "https://pypi.org/pypi/claude-openrouter/json"


def _inside(path: Path, directory: Path) -> bool:
    try:
        path.absolute().relative_to(directory.expanduser().absolute())
    except ValueError:
        return False
    return True


def _command_stdout(command: list[str]) -> str | None:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    return output or None


def _fallback_tool_dir() -> Path:
    configured = os.environ.get("CLAUDE_OPENROUTER_TOOL_DIR")
    if configured:
        return Path(configured)
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data_home / PACKAGE_NAME / "tool"


def _is_editable_install() -> bool:
    try:
        direct_url = metadata.distribution(PACKAGE_NAME).read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return False
    if not direct_url:
        return False
    try:
        document = json.loads(direct_url)
    except (TypeError, json.JSONDecodeError):
        return False
    return bool(document.get("dir_info", {}).get("editable"))


def _source_install_location() -> str | None:
    """Return a direct source location that a registry update must not replace."""
    try:
        direct_url = metadata.distribution(PACKAGE_NAME).read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return None
    if not direct_url:
        return None
    try:
        document = json.loads(direct_url)
    except (TypeError, json.JSONDecodeError):
        return None
    if "dir_info" not in document and "vcs_info" not in document:
        return None
    url = document.get("url")
    if not isinstance(url, str) or not url:
        return "an unknown source checkout"
    parsed = urlsplit(url)
    if parsed.scheme == "file":
        return unquote(parsed.path)
    return url


def _stable_version_tuple(value: str) -> tuple[int, ...] | None:
    if re.fullmatch(r"\d+(?:\.\d+)*", value) is None:
        return None
    return tuple(int(part) for part in value.split("."))


def _published_version() -> str | None:
    """Return the latest stable PyPI version when using the default registry."""
    index_url = os.environ.get("CLAUDE_OPENROUTER_PYPI_INDEX_URL", DEFAULT_INDEX_URL)
    if index_url.rstrip("/") != DEFAULT_INDEX_URL.rstrip("/"):
        return None
    request = urllib.request.Request(
        PYPI_JSON_URL,
        headers={"User-Agent": f"{PACKAGE_NAME}-updater"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            document = json.load(response)
    except (OSError, ValueError, urllib.error.URLError):
        return None
    info = document.get("info") if isinstance(document, dict) else None
    version = info.get("version") if isinstance(info, dict) else None
    return version if isinstance(version, str) and version else None


def _pip_command(*, user: bool = False) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-cache-dir",
        "--upgrade",
        "--index-url",
        os.environ.get("CLAUDE_OPENROUTER_PYPI_INDEX_URL", DEFAULT_INDEX_URL),
    ]
    if user:
        command.append("--user")
    command.append(PACKAGE_NAME)
    return command


def _upgrade_command() -> list[str]:
    """Return an in-place upgrade command for the environment running clor."""
    environment = Path(sys.prefix)
    index_url = os.environ.get("CLAUDE_OPENROUTER_PYPI_INDEX_URL", DEFAULT_INDEX_URL)

    uv = shutil.which("uv")
    if uv:
        uv_tools = _command_stdout([uv, "tool", "dir"])
        if uv_tools and _inside(environment, Path(uv_tools)):
            return [
                uv,
                "tool",
                "install",
                "--force",
                "--link-mode",
                "copy",
                "--refresh-package",
                PACKAGE_NAME,
                "--default-index",
                index_url,
                PACKAGE_NAME,
            ]

    if _inside(environment, _fallback_tool_dir()):
        return _pip_command()

    pipx = shutil.which("pipx")
    if pipx:
        pipx_venvs = _command_stdout([pipx, "environment", "--value", "PIPX_LOCAL_VENVS"])
        if pipx_venvs and _inside(environment, Path(pipx_venvs)):
            return [pipx, "upgrade", PACKAGE_NAME, "--index-url", index_url]

    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        if _is_editable_install():
            raise RuntimeError(
                "this is an editable development install; update it with its source checkout"
            )
        return _pip_command()

    try:
        package_root = Path(metadata.distribution(PACKAGE_NAME).locate_file(""))
        user_site = Path(site.getusersitepackages())
    except (metadata.PackageNotFoundError, TypeError):
        package_root = Path("/")
        user_site = Path("/dev/null")
    if _inside(package_root, user_site):
        return _pip_command(user=True)

    raise RuntimeError(
        "this install is not managed by uv, pipx, the curl installer, or a private Python "
        "environment; update it with the package manager that installed it"
    )


def _installed_version() -> str:
    scripts = Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin")
    candidates = [scripts / "clor", scripts / "claude-openrouter"]
    for name in ("clor", "claude-openrouter"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))

    seen: set[Path] = set()
    for executable in candidates:
        if executable in seen or not executable.is_file():
            continue
        seen.add(executable)
        result = subprocess.run(
            [str(executable), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            continue
        reported = result.stdout.strip()
        version = reported.removeprefix(f"{PACKAGE_NAME} ")
        if version and not re.search(r"\s|[\x00-\x1f\x7f]", version):
            return version
    raise RuntimeError("the updated clor executable could not be verified")


def update_installed_package(previous_version: str) -> None:
    """Upgrade clor in place and report the version transition."""
    print("Checking for the latest Claude OpenRouter release…")
    source = _source_install_location()
    if source is not None:
        published = _published_version()
        current_tuple = _stable_version_tuple(previous_version)
        published_tuple = _stable_version_tuple(published) if published else None
        if current_tuple is None or published_tuple is None:
            raise RuntimeError(
                f"this clor build is installed from source at {source}; the published version "
                "could not be compared safely, so the registry update was refused"
            )
        if published_tuple <= current_tuple:
            relation = "matches" if published_tuple == current_tuple else "is ahead of"
            print(
                f"Claude OpenRouter source build {previous_version} {relation} the latest "
                f"published release ({published}); no registry update needed."
            )
            return
    command = _upgrade_command()
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Claude OpenRouter update exited with status {result.returncode}")
    installed_version = _installed_version()
    if installed_version == previous_version:
        print(f"Claude OpenRouter is already up to date at {installed_version}.")
    else:
        print(f"Updated Claude OpenRouter from {previous_version} to {installed_version}.")
