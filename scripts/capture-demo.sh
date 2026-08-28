#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "$script_dir/.." && pwd)"
asset_dir="$repo_dir/docs/assets"
demo_root="$(mktemp -d "${TMPDIR:-/tmp}/claude-openrouter-demo.XXXXXXXX")"

cleanup() {
  rm -rf -- "$demo_root"
}
trap cleanup EXIT HUP INT TERM

for command_name in asciinema claude expect tmux uv; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "$command_name is required to capture the demo" >&2
    exit 2
  }
done

mkdir -p "$asset_dir"
export UV_TOOL_DIR="$demo_root/.local/share/uv/tools"
export UV_TOOL_BIN_DIR="$demo_root/.local/bin"
export PATH="$UV_TOOL_BIN_DIR:$PATH"
export TERM="xterm-256color"
export COLORTERM="truecolor"
export FORCE_COLOR="3"
export XDG_CONFIG_HOME="$demo_root/.config"
export XDG_CACHE_HOME="$demo_root/.cache"
export XDG_STATE_HOME="$demo_root/.local/state"
export XDG_DATA_HOME="$demo_root/.local/share"
export CLAUDE_CONFIG_DIR="$demo_root/.claude"
export CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT="1"
export CLOR_DEMO_INSTALL_URL="${CLOR_DEMO_INSTALL_URL:-https://xhluca.github.io/claude-openrouter/install.sh}"
unset ANTHROPIC_AUTH_TOKEN ANTHROPIC_API_KEY CLAUDE_CODE_OAUTH_TOKEN

key_source="${CLOR_DEMO_KEY_FILE:-}"
default_credential="${HOME}/.config/claude-openrouter/credential"
demo_key_file="$demo_root/openrouter-key"
if [[ -n "$key_source" && -s "$key_source" ]]; then
  install -m 600 "$key_source" "$demo_key_file"
elif [[ -n "${OPENROUTER_API_KEY:-}" ]]; then
  umask 077
  printf '%s\n' "$OPENROUTER_API_KEY" > "$demo_key_file"
elif [[ -s "$default_credential" ]]; then
  install -m 600 "$default_credential" "$demo_key_file"
else
  echo "a funded OpenRouter key is required for the live Claude response" >&2
  echo "set OPENROUTER_API_KEY or CLOR_DEMO_KEY_FILE, then rerun this script" >&2
  exit 2
fi
grep -Eq '^sk-or-[^[:space:]]{10,}$' "$demo_key_file" || {
  echo "the demo OpenRouter key has an unexpected format" >&2
  exit 2
}
unset OPENROUTER_API_KEY
export CLOR_DEMO_KEY_FILE="$demo_key_file"
export CLOR_DEMO_ROOT="$demo_root"
export CLOR_DEMO_SNAPSHOT_FILE="$demo_root/demo-final.ansi"

python3 "$script_dir/prepare-demo.py" "$CLAUDE_CONFIG_DIR" "$repo_dir"

claude_credentials_source="${CLOR_DEMO_CLAUDE_CREDENTIALS_FILE:-${HOME}/.claude/.credentials.json}"
if [[ ! -s "$claude_credentials_source" ]]; then
  echo "a native Claude.ai login is required to demonstrate connector-compatible auth" >&2
  echo "run claude /login or set CLOR_DEMO_CLAUDE_CREDENTIALS_FILE" >&2
  exit 2
fi
install -m 600 "$claude_credentials_source" "$CLAUDE_CONFIG_DIR/.credentials.json"
CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR" claude auth status --json >/dev/null

asciinema rec \
  --quiet \
  --overwrite \
    --cols 75 \
    --rows 24 \
  --idle-time-limit 3 \
  --title "Claude OpenRouter — curl install to live GLM-5.3 response" \
  --command "$script_dir/capture-demo.exp" \
  "$asset_dir/demo.cast"

if grep -Fiq 'did not finish the live Claude response' "$asset_dir/demo.cast"; then
  echo "demo capture did not finish the live Claude response" >&2
  exit 1
fi

stabilize_args=("$asset_dir/demo.cast")
if [[ -s "$CLOR_DEMO_SNAPSHOT_FILE" ]]; then
  stabilize_args+=(--snapshot "$CLOR_DEMO_SNAPSHOT_FILE")
fi
python3 "$script_dir/stabilize-demo-cast.py" "${stabilize_args[@]}"
python3 "$script_dir/sanitize-demo-cast.py" \
  "$asset_dir/demo.cast" \
  --secret-file "$demo_key_file"
python3 "$script_dir/verify-demo-session.py" \
  "$CLAUDE_CONFIG_DIR" \
  "publicly available"

selected_model="$(python3 -c 'import json, sys; print(",".join(json.load(open(sys.argv[1], encoding="utf-8"))["favorites"]))' "$XDG_CONFIG_HOME/claude-openrouter/config.json")"
if [[ "$selected_model" != "z-ai/glm-5.3" ]]; then
  echo "demo selected the wrong OpenRouter model: $selected_model" >&2
  exit 1
fi

if grep -Eq 'sk-or-[A-Za-z0-9_-]+' "$asset_dir/demo.cast"; then
  echo "refusing to publish a cast containing a key-shaped string" >&2
  exit 1
fi
if grep -Fq 'connectors are disabled' "$asset_dir/demo.cast"; then
  echo "refusing to publish a demo with Claude.ai connectors disabled" >&2
  exit 1
fi
if grep -Fiq 'did not finish the live Claude response' "$asset_dir/demo.cast"; then
  echo "refusing to publish an incomplete Claude response" >&2
  exit 1
fi
if grep -Eiq 'Interrupted|What should Claude do instead' "$asset_dir/demo.cast"; then
  echo "refusing to publish an interrupted Claude response" >&2
  exit 1
fi

python3 "$script_dir/verify-demo-cast.py" \
  "$asset_dir/demo.cast" \
  'curl -LsSf https://xhluca.github.io/claude-openrouter/install.sh' \
  'OpenRouter API key:' \
  'choose /model favorites' \
  'z-ai/glm-5.3' \
  'Claude OpenRouter is ready' \
  'claude' \
  'GLM 5.3' \
  'What model powers you' \
  'weights publicly available'

echo "Captured $asset_dir/demo.cast"
