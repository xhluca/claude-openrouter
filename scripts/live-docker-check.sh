#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 /path/to/openrouter-key /path/to/claude-config-dir" >&2
  exit 2
fi

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
openrouter_key="$(realpath "$1")"
claude_config="$(realpath "$2")"
claude_binary="$(readlink -f "$(command -v claude)")"

[[ -f "$openrouter_key" ]] || { echo "OpenRouter key file is missing" >&2; exit 1; }
[[ -f "$claude_config/.credentials.json" ]] || {
  echo "Claude OAuth credential is missing" >&2
  exit 1
}

docker_home="$(mktemp -d "${TMPDIR:-/tmp}/clor-docker-home.XXXXXXXX")"
cleanup() {
  rm -rf -- "$docker_home"
}
trap cleanup EXIT

mkdir -p "$docker_home/.claude"
install -m 600 "$claude_config/.credentials.json" "$docker_home/.claude/.credentials.json"
if [[ -f "$HOME/.claude.json" ]]; then
  install -m 600 "$HOME/.claude.json" "$docker_home/.claude.json"
fi

image="claude-openrouter-live:local"
docker build --quiet --file "$repo_dir/docker/Dockerfile.integration" --tag "$image" "$repo_dir"

docker run --rm --interactive --user "$(id -u):$(id -g)" \
  --hostname clor-docker \
  --env HOME=/root \
  --mount "type=bind,src=$docker_home,dst=/root" \
  --mount "type=bind,src=$openrouter_key,dst=/run/secrets/openrouter,readonly" \
  --mount "type=bind,src=$claude_binary,dst=/usr/local/bin/claude,readonly" \
  "$image" bash -s <<'CONTAINER'
set -euo pipefail

mkdir -p /workspace
printf 'FROM_DOCKER\n' > /workspace/CLAUDE.md
python - <<'PY'
import json
from pathlib import Path
p=Path('/root/.claude.json')
x=json.loads(p.read_text()) if p.exists() else {}
x['hasCompletedOnboarding']=True
x.setdefault('projects', {})['/workspace']={
    'allowedTools': [],
    'mcpContextUris': [],
    'mcpServers': {},
    'enabledMcpjsonServers': [],
    'disabledMcpjsonServers': [],
    'hasTrustDialogAccepted': True,
    'hasClaudeMdExternalIncludesApproved': False,
    'hasClaudeMdExternalIncludesWarningShown': False,
}
p.write_text(json.dumps(x))
p.chmod(0o600)
settings=Path('/root/.claude/settings.json')
settings.write_text(json.dumps({'theme': 'dark', 'autoUpdates': False}))
settings.chmod(0o600)
PY

# Establish the native Claude/Max control before clor changes any settings.
baseline_status=0
baseline="$(claude -p --model sonnet --tools '' --no-session-persistence \
  'Reply with exactly CLOR_MAX_OK and nothing else.' \
  </dev/null 2>/tmp/baseline-native-error.txt)" \
  || baseline_status=$?
if [[ $baseline_status -ne 0 ]]; then
  if [[ "$baseline" == *"weekly limit"* ]]; then
    baseline_result=quota
    echo BASELINE_CLAUDE_MAX_QUOTA_BLOCKED
  else
    printf '%s\n' "$baseline" >&2
    sed -n '1,120p' /tmp/baseline-native-error.txt >&2
    exit "$baseline_status"
  fi
else
  [[ "$baseline" == *CLOR_MAX_OK* ]] \
    || { echo "baseline native response check failed" >&2; exit 1; }
  baseline_result=success
  echo BASELINE_CLAUDE_MAX_OK
fi
cp /root/.claude/.credentials.json /tmp/native-credential-before-clor.json
chmod 0600 /tmp/native-credential-before-clor.json

clor setup --key-stdin --models \
  z-ai/glm-5.3-flash '~deepseek/deepseek-v4-flash-latest' \
  < /run/secrets/openrouter
cmp /tmp/native-credential-before-clor.json /root/.claude/.credentials.json
echo NATIVE_CREDENTIAL_UNCHANGED_BY_CLOR
clor doctor --json > /tmp/doctor.json
python - <<'PY'
import json
x=json.load(open('/tmp/doctor.json'))
assert x['configured'] and x['router'] and x['native_login'], x
assert x['anthropic_auth'] == 'max', x
print('DOCKER_DOCTOR_OK')
PY

clor search glm-5.3 --tools > /tmp/tool-search.txt
grep -q '^z-ai/glm-5.3-flash' /tmp/tool-search.txt
echo OPENROUTER_TOOL_SEARCH_OK
if ! clor check z-ai/glm-5.3-flash > /tmp/tool-check.txt 2>/tmp/tool-check.error; then
  sed -n '1,160p' /tmp/tool-check.txt >&2
  sed -n '1,160p' /tmp/tool-check.error >&2
  exit 1
fi
grep -q 'Tool round-trip passed' /tmp/tool-check.txt
echo CLAUDE_CODE_TOOL_ROUND_TRIP_OK

native_status=0
native="$(claude -p --model sonnet --tools '' --no-session-persistence \
  'Reply with exactly CLOR_MAX_OK and nothing else.' \
  </dev/null 2>/tmp/native-error.txt)" \
  || native_status=$?
if [[ $native_status -ne 0 ]]; then
  if [[ "$native" == *"weekly limit"* ]]; then
    native_result=quota
    echo CLAUDE_MAX_ROUTE_QUOTA_BLOCKED
  else
    printf '%s\n' "$native" >&2
    sed -n '1,120p' /tmp/native-error.txt >&2
    exit "$native_status"
  fi
else
  [[ "$native" == *CLOR_MAX_OK* ]] \
    || { echo "native response check failed" >&2; exit 1; }
  native_result=success
fi
[[ "$native_result" == "$baseline_result" ]] || {
  echo "native Max outcome changed after adding the OpenRouter key" >&2
  exit 1
}
echo CLAUDE_MAX_OUTCOME_UNCHANGED_BY_OPENROUTER
python - <<'PY'
import json
from pathlib import Path
p=Path('/root/.local/state/claude-openrouter/router-status.json')
x=json.loads(p.read_text())
assert x['route'] == 'anthropic', x
assert x['model'].startswith('claude-'), x
print('CLAUDE_MAX_ROUTE_OK')
PY

openrouter_status=0
openrouter="$(claude -p --model clor/openrouter/z-ai/glm-5.3-flash \
  --tools '' --no-session-persistence \
  'Reply with exactly CLOR_OPENROUTER_OK and nothing else.' \
  </dev/null 2>/tmp/openrouter-error.txt)" \
  || openrouter_status=$?
if [[ $openrouter_status -ne 0 ]]; then
  printf '%s\n' "$openrouter" >&2
  sed -n '1,120p' /tmp/openrouter-error.txt >&2
  test ! -f /root/.local/state/claude-openrouter/router-status.json \
    || sed -n '1,120p' /root/.local/state/claude-openrouter/router-status.json >&2
  exit "$openrouter_status"
fi
[[ "$openrouter" == *CLOR_OPENROUTER_OK* ]] || {
  echo "OpenRouter response check failed" >&2
  exit 1
}
python - <<'PY'
import json
from pathlib import Path
p=Path('/root/.local/state/claude-openrouter/router-status.json')
x=json.loads(p.read_text())
assert x['route'] == 'openrouter', x
assert x['model'] == 'z-ai/glm-5.3-flash', x
print('OPENROUTER_GLM_ROUTE_OK')
PY

python - <<'PY'
import json
from pathlib import Path
manifest=json.loads(Path('/root/.config/claude-openrouter/subagents.json').read_text())
by_model={entry['model']: name for name, entry in manifest['agents'].items()}
expected={
    'clor/openrouter/z-ai/glm-5.3-flash': '/tmp/glm-agent-name',
    'clor/openrouter/~deepseek/deepseek-v4-flash-latest': '/tmp/deepseek-agent-name',
}
assert set(expected) <= set(by_model), by_model
for model, destination in expected.items():
    Path(destination).write_text(by_model[model])
print('OPENROUTER_SUBAGENT_DEFINITIONS_OK')
PY

glm_agent="$(cat /tmp/glm-agent-name)"
deepseek_agent="$(cat /tmp/deepseek-agent-name)"

claude -p --model clor/openrouter/z-ai/glm-5.3-flash \
  --permission-mode bypassPermissions --tools Agent --no-session-persistence \
  --debug-file /tmp/glm-to-deepseek.log --output-format json \
  "Invoke the ${deepseek_agent} subagent exactly once. Tell it to reply with exactly CHILD_DEEPSEEK_OK. Deliberately set the Agent model parameter to sonnet; the clor hook must preserve the named subagent's exact model instead. Return its result." \
  </dev/null > /tmp/glm-to-deepseek.json
python - <<'PY'
import json
x=json.load(open('/tmp/glm-to-deepseek.json'))
assert not x['is_error'], x
assert 'CHILD_DEEPSEEK_OK' in x['result'], x['result']
usage=x['modelUsage']
assert 'clor/openrouter/z-ai/glm-5.3-flash' in usage, usage
assert 'clor/openrouter/~deepseek/deepseek-v4-flash-latest' in usage, usage
assert not any(model.startswith('claude-') for model in usage), usage
print('GLM_PARENT_TO_DEEPSEEK_SUBAGENT_OK')
PY

claude -p --model 'clor/openrouter/~deepseek/deepseek-v4-flash-latest' \
  --permission-mode bypassPermissions --tools Agent --no-session-persistence \
  --debug-file /tmp/deepseek-to-glm.log --output-format json \
  "Invoke the ${glm_agent} subagent exactly once. Tell it to reply with exactly CHILD_GLM_OK. Deliberately set the Agent model parameter to sonnet; the clor hook must preserve the named subagent's exact model instead. Return its result." \
  </dev/null > /tmp/deepseek-to-glm.json
python - <<'PY'
import json
x=json.load(open('/tmp/deepseek-to-glm.json'))
assert not x['is_error'], x
assert 'CHILD_GLM_OK' in x['result'], x['result']
usage=x['modelUsage']
assert 'clor/openrouter/~deepseek/deepseek-v4-flash-latest' in usage, usage
assert 'clor/openrouter/z-ai/glm-5.3-flash' in usage, usage
assert not any(model.startswith('claude-') for model in usage), usage
print('DEEPSEEK_PARENT_TO_GLM_SUBAGENT_OK')
PY

expect <<'EXPECT' > /dev/null
set timeout 15
log_user 1
log_file /tmp/model-picker.terminal
spawn -noecho claude
after 3500
send "/model\r"
after 3000
send "\003"
after 500
send "\003"
expect eof
EXPECT
python - <<'PY'
import re
from pathlib import Path
text=Path('/tmp/model-picker.terminal').read_text(errors='replace')
text=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
assert 'OpenRouter' in text, text[-4000:]
assert any(name in text for name in ('Opus', 'Sonnet', 'Haiku')), text[-4000:]
print('CLAUDE_MIXED_MODEL_PICKER_OK')
PY

background_output="$(claude --background \
  --model clor/openrouter/z-ai/glm-5.3-flash \
  --permission-mode dontAsk \
  'Reply with exactly CLOR_AGENT_VIEW_OK and nothing else.' </dev/null)"
printf '%s\n' "$background_output" > /tmp/background-output.txt

agent_ready=0
for _ in $(seq 1 60); do
  claude agents --json --all </dev/null > /tmp/agents.json
  if python - <<'PY'
import json
from pathlib import Path
x=json.load(open('/tmp/agents.json'))
for session in x:
    session_id=session.get('sessionId')
    if not session_id or session.get('status') not in {'completed', 'idle'}:
        continue
    for path in Path('/root/.claude/projects').rglob(f'{session_id}.jsonl'):
        for line in path.read_text(errors='replace').splitlines():
            try:
                event=json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get('type') != 'assistant':
                continue
            for block in event.get('message', {}).get('content', []):
                if isinstance(block, dict) and 'CLOR_AGENT_VIEW_OK' in str(block.get('text', '')):
                    raise SystemExit(0)
raise SystemExit(1)
PY
  then
    agent_ready=1
    break
  fi
  sleep 1
done

if [[ $agent_ready -ne 1 ]]; then
  python - <<'PY' >&2
import json
import re
from pathlib import Path
agents=json.load(open('/tmp/agents.json'))
print('Agent status:', agents)
for session in agents:
    session_id=session.get('sessionId')
    job=Path('/root/.claude/jobs') / str(session.get('id')) / 'state.json'
    if job.exists():
        state=json.loads(job.read_text())
        print('Job state:', {
            key: state.get(key)
            for key in ('state', 'detail', 'output', 'needs')
            if key in state
        })
    for path in Path('/root/.claude/projects').rglob(f'{session_id}.jsonl'):
        for line in path.read_text(errors='replace').splitlines():
            try:
                event=json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get('type') == 'system':
                content=str(event.get('content', ''))
                content=re.sub(r'sk-[A-Za-z0-9_-]+', '<redacted>', content)
                print('System event:', event.get('subtype'), content[:500])
daemon=Path('/root/.claude/daemon.log')
if daemon.exists():
    text=re.sub(r'sk-[A-Za-z0-9_-]+', '<redacted>', daemon.read_text(errors='replace'))
    print('Daemon tail:', text[-3000:])
PY
  exit 1
fi

python - <<'PY'
import json
from pathlib import Path
x=json.load(open('/tmp/agents.json'))
assert x, x
session=x[0]
assert session.get('status') in {'completed','idle'}, x
session_id=session['sessionId']
turns=[]
for path in Path('/root/.claude/projects').rglob(f'{session_id}.jsonl'):
    for line in path.read_text(errors='replace').splitlines():
        try:
            event=json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get('type') != 'assistant':
            continue
        message=event.get('message', {})
        for block in message.get('content', []):
            if isinstance(block, dict) and block.get('type') == 'text':
                turns.append(str(block.get('text', '')))
assert any('CLOR_AGENT_VIEW_OK' in turn for turn in turns), turns
print('AGENT_VIEW_BACKGROUND_OK')
PY
python - <<'PY'
import json
from pathlib import Path
p=Path('/root/.local/state/claude-openrouter/router-status.json')
x=json.loads(p.read_text())
assert x['route'] == 'openrouter', x
assert x['model'] == 'z-ai/glm-5.3-flash', x
print('AGENT_VIEW_GLM_ROUTE_OK')
PY

expect <<'EXPECT' > /dev/null
set timeout 12
log_user 1
log_file /tmp/agent-view.terminal
spawn -noecho claude agents --model clor/openrouter/z-ai/glm-5.3-flash
after 4000
send "\003"
expect eof
EXPECT

test -s /tmp/agent-view.terminal
python - <<'PY'
import re
from pathlib import Path
text=Path('/tmp/agent-view.terminal').read_text(errors='replace')
text=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
assert 'agent' in text.casefold(), text[-2000:]
print('AGENT_VIEW_SCREEN_RENDERED_OK')
PY
echo AGENT_VIEW_TUI_OK

clor reset
test ! -e /root/.config/claude-openrouter/credential
echo CLOR_RESET_OK
CONTAINER
