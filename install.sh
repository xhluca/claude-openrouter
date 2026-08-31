#!/bin/sh
# POSIX user-level installer for Claude OpenRouter.

set -eu

package_name="claude-openrouter"
package_version="0.4.3"
wheel_name="claude_openrouter-${package_version}-py3-none-any.whl"
# Filled from the release artifact by scripts/build-release.sh.
wheel_sha256="1dec732fe6f5ed4237eae457897ad85877be2f4b0a89c762612df2d2b7739627"
pypi_index_url="${CLAUDE_OPENROUTER_PYPI_INDEX_URL:-https://pypi.org/simple}"
release_base_url="${CLAUDE_OPENROUTER_INSTALL_BASE_URL:-https://xhluca.github.io/claude-openrouter/releases/${package_version}}"
package_spec="${package_name}==${package_version}"

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Install Claude OpenRouter for the current user and run its guided setup.

Usage:
  curl -LsSf https://xhluca.github.io/claude-openrouter/install.sh | sh
  sh install.sh [options]

Options:
  --install-only        Install the CLI without asking for a key or models.
  --skip-claude-install Fail instead of installing Claude Code when it is missing.
  -h, --help            Show this help.

The installer never accepts an API key as a command-line argument. Guided setup
uses a masked terminal prompt and stores the key in a mode-0600 credential file.
EOF
}

install_only=0
skip_claude_install=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --install-only) install_only=1; shift ;;
    --skip-claude-install) skip_claude_install=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (run with --help)" ;;
  esac
done

script_dir=""
case "$0" in
  */*) script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" 2>/dev/null && pwd -P) || script_dir='' ;;
  *)
    if [ -f "./$0" ]; then
      script_dir=$(pwd -P)
    fi
    ;;
esac

temporary_dir=""
temporary_wheel=""
wheel_path=""
cleanup() {
  if [ -n "$temporary_wheel" ] && [ -f "$temporary_wheel" ]; then
    rm -f -- "$temporary_wheel"
  fi
  if [ -n "$temporary_dir" ] && [ -d "$temporary_dir" ]; then
    rmdir -- "$temporary_dir" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'cleanup; exit 1' HUP INT TERM

verify_wheel() {
  checked_wheel=$1
  [ "$wheel_sha256" != "TO_BE_REPLACED" ] || die "release checksum is not configured"
  if command -v sha256sum >/dev/null 2>&1; then
    actual_sha256=$(sha256sum "$checked_wheel" | awk '{print $1}')
  elif command -v shasum >/dev/null 2>&1; then
    actual_sha256=$(shasum -a 256 "$checked_wheel" | awk '{print $1}')
  elif command -v openssl >/dev/null 2>&1; then
    actual_sha256=$(openssl dgst -sha256 "$checked_wheel" | awk '{print $NF}')
  else
    die "sha256sum, shasum, or openssl is required to verify the release"
  fi
  [ "$actual_sha256" = "$wheel_sha256" ] || die "release checksum mismatch"
  printf 'Verified fallback release checksum.\n'
}

prepare_fallback_wheel() {
  [ -z "$wheel_path" ] || return
  temporary_dir=$(mktemp -d "${TMPDIR:-/tmp}/claude-openrouter.XXXXXXXX")
  temporary_wheel="$temporary_dir/$wheel_name"
  wheel_url="$release_base_url/$wheel_name"
  printf 'Downloading checksum-pinned fallback for %s %s...\n' "$package_name" "$package_version"
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf --retry 3 --output "$temporary_wheel" "$wheel_url"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$temporary_wheel" "$wheel_url"
  else
    die "curl or wget is required to download the release"
  fi
  wheel_path="$temporary_wheel"
  verify_wheel "$wheel_path"
}

local_source=""
if [ -n "$script_dir" ] && [ -f "$script_dir/pyproject.toml" ]; then
  local_source="$script_dir"
fi
if [ -n "$script_dir" ] && [ -f "$script_dir/dist/$wheel_name" ]; then
  wheel_path="$script_dir/dist/$wheel_name"
  verify_wheel "$wheel_path"
fi

if ! command -v claude >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/claude" ]; then
  [ "$skip_claude_install" -eq 0 ] || die "Claude Code is not installed"
  command -v curl >/dev/null 2>&1 || die "curl is required to install Claude Code"
  printf 'Installing Claude Code from its official installer...\n'
  curl -fsSL https://claude.ai/install.sh | bash
fi

printf 'Installing %s %s for user %s...\n' "$package_name" "$package_version" "$(id -un)"
installed_command=""

if command -v uv >/dev/null 2>&1; then
  if [ -n "$local_source" ]; then
    uv tool install --force --link-mode copy "$local_source"
  elif [ -n "$wheel_path" ]; then
    uv tool install --force --link-mode copy "$wheel_path"
  elif ! uv tool install --force --link-mode copy --refresh-package "$package_name" \
    --default-index "$pypi_index_url" "$package_spec"; then
    printf 'PyPI installation failed; using the verified release fallback.\n' >&2
    prepare_fallback_wheel
    uv tool install --force --link-mode copy "$wheel_path"
  fi
  uv_bin_dir="${UV_TOOL_BIN_DIR:-$HOME/.local/bin}"
  if [ -x "$uv_bin_dir/claude-openrouter" ]; then
    installed_command="$uv_bin_dir/claude-openrouter"
  elif command -v claude-openrouter >/dev/null 2>&1; then
    installed_command=$(command -v claude-openrouter)
  fi
else
  python="${PYTHON:-python3}"
  command -v "$python" >/dev/null 2>&1 || die "Python 3.10+ or uv is required"
  "$python" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' || \
    die "Python 3.10 or newer is required"
  data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
  bin_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"
  install_dir="${CLAUDE_OPENROUTER_TOOL_DIR:-$data_home/claude-openrouter/tool}"
  mkdir -p -- "$install_dir" "$bin_dir"
  for destination in "$bin_dir/claude-openrouter" "$bin_dir/clor"; do
    if [ -e "$destination" ] && [ ! -L "$destination" ]; then
      die "refusing to replace existing file: $destination"
    fi
  done
  "$python" -m venv "$install_dir"
  if [ -n "$local_source" ]; then
    "$install_dir/bin/python" -m pip install --disable-pip-version-check --force-reinstall "$local_source"
  elif [ -n "$wheel_path" ]; then
    "$install_dir/bin/python" -m pip install --disable-pip-version-check --force-reinstall "$wheel_path"
  elif ! "$install_dir/bin/python" -m pip install --disable-pip-version-check \
    --index-url "$pypi_index_url" --force-reinstall "$package_spec"; then
    printf 'PyPI installation failed; using the verified release fallback.\n' >&2
    prepare_fallback_wheel
    "$install_dir/bin/python" -m pip install --disable-pip-version-check --force-reinstall "$wheel_path"
  fi
  ln -sfn -- "$install_dir/bin/claude-openrouter" "$bin_dir/claude-openrouter"
  ln -sfn -- "$install_dir/bin/clor" "$bin_dir/clor"
  installed_command="$bin_dir/claude-openrouter"
fi

[ -n "$installed_command" ] && [ -x "$installed_command" ] || \
  die "installation completed but claude-openrouter was not found"
printf 'Installed commands: %s and clor\n' "$installed_command"

if [ "$install_only" -eq 1 ]; then
  printf 'Installation complete. Run: %s setup\n' "$installed_command"
  exit 0
fi

if [ -r /dev/tty ]; then
  "$installed_command" setup </dev/tty
else
  die "interactive setup needs a terminal; rerun claude-openrouter setup directly"
fi

printf '\nClaude OpenRouter is ready. Run: claude\n'
