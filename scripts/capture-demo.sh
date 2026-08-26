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

for command_name in asciinema claude expect uv; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "$command_name is required to capture the demo" >&2
    exit 2
  }
done

mkdir -p "$asset_dir"
uv sync --quiet --directory "$repo_dir"

export PATH="$repo_dir/.venv/bin:$PATH"
export TERM="xterm-256color"
export XDG_CONFIG_HOME="$demo_root/.config"
export XDG_CACHE_HOME="$demo_root/.cache"
export XDG_STATE_HOME="$demo_root/.local/state"
export CLAUDE_CONFIG_DIR="$demo_root/.claude"
export CLOR_DEMO_KEY="sk-or-v1-public-catalog-demo-not-a-secret"
unset ANTHROPIC_AUTH_TOKEN ANTHROPIC_API_KEY CLAUDE_CODE_OAUTH_TOKEN

python3 "$script_dir/prepare-demo.py" "$CLAUDE_CONFIG_DIR" "$repo_dir"

asciinema rec \
  --quiet \
  --overwrite \
  --cols 100 \
  --rows 34 \
  --idle-time-limit 3 \
  --title "Claude OpenRouter — real CLI and Claude Code /model picker" \
  --command "$script_dir/capture-demo.exp" \
  "$asset_dir/demo.cast"

python3 "$script_dir/sanitize-demo-cast.py" "$asset_dir/demo.cast"

if grep -Eq 'sk-or-[A-Za-z0-9_-]+' "$asset_dir/demo.cast"; then
  echo "refusing to publish a cast containing a key-shaped string" >&2
  exit 1
fi

echo "Captured $asset_dir/demo.cast"
