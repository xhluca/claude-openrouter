#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "$script_dir/.." && pwd)"
asset_dir="$repo_dir/docs/assets"
demo_root="$(mktemp -d "${TMPDIR:-/tmp}/claude-openrouter-demo-docker.XXXXXXXX")"

cleanup() {
  rm -rf -- "$demo_root"
}
trap cleanup EXIT HUP INT TERM

for command_name in claude docker python3; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "$command_name is required to capture the Docker demo" >&2
    exit 2
  }
done

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
  echo "a funded OpenRouter key is required for the live Docker demo" >&2
  echo "set OPENROUTER_API_KEY or CLOR_DEMO_KEY_FILE, then rerun this script" >&2
  exit 2
fi
grep -Eq '^sk-or-[^[:space:]]{10,}$' "$demo_key_file" || {
  echo "the demo OpenRouter key has an unexpected format" >&2
  exit 2
}
unset OPENROUTER_API_KEY

claude_credentials_source="${CLOR_DEMO_CLAUDE_CREDENTIALS_FILE:-${HOME}/.claude/.credentials.json}"
if [[ ! -s "$claude_credentials_source" ]]; then
  echo "a native Claude.ai login is required for the Docker demo" >&2
  echo "run claude auth login or set CLOR_DEMO_CLAUDE_CREDENTIALS_FILE" >&2
  exit 2
fi

demo_home="$demo_root/home"
mkdir -p "$demo_home/.claude" "$asset_dir"
python3 "$script_dir/prepare-demo.py" "$demo_home/.claude" /workspace
install -m 600 "$claude_credentials_source" "$demo_home/.claude/.credentials.json"

claude_binary="$(readlink -f "$(command -v claude)")"
image="claude-openrouter-demo:local"
docker build --quiet \
  --build-arg "DEMO_UID=$(id -u)" \
  --build-arg "DEMO_GID=$(id -g)" \
  --file "$repo_dir/docker/Dockerfile.demo" \
  --tag "$image" \
  "$repo_dir" >/dev/null

docker run --rm --interactive \
  --hostname clor-demo \
  --env HOME=/home/demo \
  --env CLAUDE_CONFIG_DIR=/home/demo/.claude \
  --env XDG_CONFIG_HOME=/home/demo/.config \
  --env XDG_CACHE_HOME=/home/demo/.cache \
  --env XDG_STATE_HOME=/home/demo/.local/state \
  --env XDG_DATA_HOME=/home/demo/.local/share \
  --env XDG_BIN_HOME=/home/demo/.local/bin \
  --env CLOR_DEMO_ROOT=/home/demo \
  --env CLOR_DEMO_KEY_FILE=/run/secrets/openrouter \
  --env CLOR_DEMO_SNAPSHOT_FILE=/output/demo-final.ansi \
  --env CLOR_DEMO_INSTALL_URL="${CLOR_DEMO_INSTALL_URL:-https://xhluca.github.io/claude-openrouter/install.sh}" \
  --env CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT=1 \
  --env TERM=xterm-256color \
  --env COLORTERM=truecolor \
  --env FORCE_COLOR=3 \
  --mount "type=bind,src=$demo_home,dst=/home/demo" \
  --mount "type=bind,src=$demo_key_file,dst=/run/secrets/openrouter,readonly" \
  --mount "type=bind,src=$claude_binary,dst=/usr/local/bin/claude,readonly" \
  --mount "type=bind,src=$repo_dir,dst=/src,readonly" \
  --mount "type=bind,src=$asset_dir,dst=/output" \
  "$image" bash -c '
    set -euo pipefail
    export PATH="$HOME/.local/bin:$PATH"
    claude auth status --json >/dev/null
    asciinema rec \
      --quiet \
      --overwrite \
      --cols 75 \
      --rows 24 \
      --idle-time-limit 3 \
      --title "Claude OpenRouter — Docker install to live GLM-5.3 answer" \
      --command /src/scripts/capture-demo.exp \
      /output/demo.cast
  '

if grep -Fiq 'did not finish the live Claude response' "$asset_dir/demo.cast"; then
  echo "demo capture did not finish the live Claude response" >&2
  exit 1
fi

stabilize_args=("$asset_dir/demo.cast")
if [[ -s "$asset_dir/demo-final.ansi" ]]; then
  stabilize_args+=(--snapshot "$asset_dir/demo-final.ansi")
fi
python3 "$script_dir/stabilize-demo-cast.py" "${stabilize_args[@]}"
rm -f -- "$asset_dir/demo-final.ansi"
python3 "$script_dir/sanitize-demo-cast.py" \
  "$asset_dir/demo.cast" \
  --secret-file "$demo_key_file"
python3 "$script_dir/verify-demo-secrets.py" \
  "$asset_dir/demo.cast" \
  "$demo_key_file" \
  "$claude_credentials_source"
python3 "$script_dir/verify-demo-session.py" \
  "$demo_home/.claude" \
  "publicly available"

selected_model="$(python3 -c 'import json, sys; print(",".join(json.load(open(sys.argv[1], encoding="utf-8"))["favorites"]))' "$demo_home/.config/claude-openrouter/config.json")"
if [[ "$selected_model" != "z-ai/glm-5.3" ]]; then
  echo "demo selected the wrong OpenRouter model: $selected_model" >&2
  exit 1
fi

if grep -Eq 'sk-(or|ant)-[A-Za-z0-9_-]+' "$asset_dir/demo.cast"; then
  echo "refusing to publish a cast containing a key-shaped string" >&2
  exit 1
fi
if grep -Fiq 'connectors are disabled' "$asset_dir/demo.cast"; then
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

echo "Captured Docker session to $asset_dir/demo.cast"
