<h1 align="center">Claude OpenRouter</h1>

<p align="center"><strong>Use OpenRouter models in Claude Code without giving up your native login or connectors.</strong></p>

<p align="center">
  <a href="https://pypi.org/project/claude-openrouter/"><img src="https://img.shields.io/pypi/v/claude-openrouter?style=flat-square&color=d97757&label=PyPI" alt="PyPI version"></a>
  <a href="https://pypi.org/project/claude-openrouter/"><img src="https://img.shields.io/pypi/pyversions/claude-openrouter?style=flat-square" alt="Python versions"></a>
  <a href="https://github.com/xhluca/claude-openrouter/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-8dbdff?style=flat-square" alt="MIT license"></a>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/xhluca/claude-openrouter/main/docs/assets/demo.gif?v=8" alt="Installing Claude OpenRouter, selecting GLM-5.3, switching models in Claude Code, and verifying its open weights" width="860">
</p>

Claude OpenRouter is a small, dependency-free CLI and loopback router. It
indexes OpenRouter's live catalog, gives you a searchable multi-select picker,
and places those favorites beside Claude's built-in models in `/model`. Native
Claude requests keep using Anthropic; explicitly labeled OpenRouter favorites
use your OpenRouter key. After setup, run the normal `claude` command.

## Install

```bash
curl -LsSf https://xhluca.github.io/claude-openrouter/install.sh | sh
```

The installer prompts for your OpenRouter key without echoing it, fetches the
current catalog, and opens the model picker immediately. It installs for the
current user on Linux or macOS. If a credential already exists, setup shows its
path and asks whether to reuse it before offering a masked replacement prompt.
Max routing requires an existing Claude.ai login; run `claude auth login` first
if Claude Code is newly installed.

Or use `uv`:

```bash
uv tool install claude-openrouter
clor setup
```

For a one-off setup without installing the package first:

```bash
uvx claude-openrouter setup
```

`pipx install claude-openrouter` works too. The full command is
`claude-openrouter`; `clor` is the shorthand. Claude Code 2.1.242 or newer is
required for the multi-model picker.

Upgrade the installation in place at any time:

```bash
clor update
```

The updater uses the manager that owns the current installation, reports the
installed version before and after, and restarts an existing hybrid service.
`clor upgrade` is an alias. When moving from an older direct-routing release to
0.4.x, run `clor setup` once to activate hybrid routing.

## Quick start

Refresh the local catalog:

```bash
clor index
```

`fetch` is an alias for `index`. `index` is the canonical name because the
result is a persistent local catalog used by selection and search.

Search refreshes the index first. Plain terms are case-insensitive substring
globs; `*`, `?`, and bracket expressions use shell-style matching:

```bash
clor search claude
clor search 'anthropic/*' '*coder*'
clor search --regex '^(anthropic|google)/.*(sonnet|gemini)'
```

Select one exact model, several exact models, or open the interactive picker:

```bash
clor select anthropic/claude-sonnet-4.6
clor select --model openai/gpt-5.4
clor select --models anthropic/claude-opus-4.6 google/gemini-3.1-pro-preview
clor select
```

The positional value is shorthand for `--model`. Each non-interactive call
replaces the saved favorite set. In the picker, type a search and press Down or
Enter to browse its results. Press Enter or Space to select and deselect; press
Up past the first result or Esc to focus the search again. Press `s` while
browsing results, or `Shift-S`/`Ctrl-S` while typing a search, to save.

Launch Claude Code normally, then switch models normally:

```bash
claude
```

```text
> /model
```

The selected rows are Claude Code's native picker rows, and model requests use
the provider shown in the row. Every local Claude Code entry point reads the
same settings, including `claude --continue`, `claude --agent NAME`, background
agents, and the `claude agents` view.

| `/model` choice | Upstream | Billing credential |
| --- | --- | --- |
| Built-in Opus, Sonnet, or Haiku | Anthropic | Claude Max OAuth by default |
| A row labeled `· OpenRouter` | OpenRouter | Stored OpenRouter key |

The router never infers a provider from a bare third-party ID. OpenRouter rows
use a private `clor/openrouter/` namespace, and an unknown or unfavorited model
fails closed. Selecting Opus cannot silently send it to OpenRouter.

Claude Code keeps its native Claude.ai authentication, so connectors remain
available and the external-auth warning is absent. If you prefer direct
Anthropic API billing for built-in Claude models, configure it separately:

```bash
clor setup --anthropic-auth api
# or, after setup:
clor config --anthropic-auth api

# Return native Claude models to Max subscription billing:
clor config --anthropic-auth max
```

The Anthropic API key is stored inside the router and is not exported as
`ANTHROPIC_API_KEY`, preserving Claude's native login and connectors. `clor
config --anthropic-key-stdin` is available for non-interactive secret input.
Deferred tool loading is disabled because non-Anthropic OpenRouter models
reject that Anthropic-specific protocol; connector tools remain available and
are loaded eagerly instead. This is required for GLM in `claude agents`.

## Commands

| Command | Purpose |
| --- | --- |
| `clor index` | Fetch and cache the current OpenRouter model catalog |
| `clor fetch` | Alias for `index` |
| `clor search QUERY...` | Refresh, then search names, IDs, and descriptions |
| `clor setup` | Run the install-time key and model setup again |
| `clor select [MODEL]` | Replace `/model` favorites exactly |
| `clor config` | Replace and validate the stored OpenRouter key |
| `clor doctor` | Check the service, favorites, and native Claude login |
| `clor claude [ARGS...]` | Compatibility alias for `claude [ARGS...]` |
| `clor update` | Install the latest release and report the version change |
| `clor reset` | Restore the original Claude settings and delete tool data |
| `clor uninstall` | Reset the integration and remove a curl/uv installation |

Every catalog command also accepts `--json` where useful. API keys are never
accepted as command-line arguments; automation can pipe one to `setup
--key-stdin` or `config --key-stdin`.

## What it changes

```text
                                    ┌─ Claude model ─────► Anthropic
Claude Code ─► 127.0.0.1 router ────┤
                                    └─ clor/openrouter/* ► OpenRouter
```

- Stores the key at `~/.config/claude-openrouter/credential` with mode `0600`.
- Runs a loopback-only router on `127.0.0.1:9417`, supervised by a systemd user
  service on Linux or a LaunchAgent on macOS. Minimal containers without a
  service manager use a detached process for the container lifetime.
- Adds the local endpoint, a random local-service token, and OpenRouter picker
  rows to `~/.claude/settings.json`. Provider API keys never appear there.
- Does not set `ANTHROPIC_API_KEY` or `apiKeyHelper` when a native login exists.
- Strips OAuth and Anthropic keys before OpenRouter requests, strips the
  OpenRouter key before Anthropic requests, and allows only selected
  OpenRouter model IDs.
- Saves the fields it replaces in a private, versioned backup and restores them
  exactly with `clor reset` or `clor uninstall`.
- Migrates settings created by older claude-openrouter releases automatically
  the next time setup or selection runs.

Claude Code itself applies one endpoint and authentication context to an entire
process. The local router supplies the missing per-model boundary while keeping
Claude Code unmodified. If the router is unavailable or cannot classify a
model, the request errors instead of falling back to another paid provider.

Claude Code is optimized for Anthropic models. OpenRouter can accept other model
IDs through its Anthropic-compatible endpoint, but models differ in tool use,
thinking blocks, context handling, and Claude Code compatibility. Prefer models
that OpenRouter documents as suitable for agentic tool use.

## Reset and uninstall

Stop the router, restore the pre-install Claude settings, and keep the command:

```bash
clor reset
```

Restore settings, remove the credential and index, and uninstall a curl/uv tool
installation:

```bash
clor uninstall
```

Reset and uninstall only reverse fields managed by Claude OpenRouter in
`~/.claude/settings.json`. They intentionally leave project-owned settings such
as `.claude/settings.local.json` unchanged. If Claude Code reports an unsafe
project permission after uninstalling, open `/permissions`, select the named
local allow rule, and remove it. Do not replace it with a broader wildcard.

## Demo

The animation above and the
[interactive replay](https://xhluca.github.io/claude-openrouter/) come from an
end-to-end Docker capture: the regular public curl installer, progressively
masked key entry, interactive search and selection of `z-ai/glm-5.3`, Claude
Code's native `/model` picker launched through plain `claude`, and the model's
live answer about its identity and open weights.

The terminal is recorded at 75×24 and rendered at 32 px into a 1480×1110 4:3
video. The 25-second installation and setup lead-in is compressed to 12 seconds;
subsequent user and UI actions stay at 1×, while Claude's active fetch/thinking
interval is accelerated 4× for pacing. `/model` is submitted as one command so
partial autocomplete redraws do not flash in the video, and capture sanitization
removes transient expanded-thinking frames without changing the final answer.

The scripts verify the exact saved favorite and assistant turn, then reject any
cast containing the OpenRouter key or mounted Claude OAuth tokens before
publishing it. Sanitization also replaces the randomized temporary path with
`~`; release-only command, settings-path, and version labels may be rebased
without altering the captured Claude interaction or response.

[Watch the MP4](https://xhluca.github.io/claude-openrouter/assets/demo.mp4?v=8),
[download the asciicast](https://xhluca.github.io/claude-openrouter/assets/demo.cast?v=8),
or [reproduce the capture](scripts/render-demo.sh).

## Development

```bash
git clone https://github.com/xhluca/claude-openrouter.git
cd claude-openrouter
uv run --with pytest pytest
uv run --with ruff ruff check .
scripts/live-docker-check.sh /path/to/openrouter-key ~/.claude
```

The Docker check mounts temporary copies of Claude OAuth state and the key only
at runtime. It makes live, billable Max and OpenRouter calls, dispatches a GLM
5.3 Flash background agent, and launches the real `claude agents` terminal UI.

## License

[MIT](LICENSE)

## Acknowledgements

[Claude Code Router (CCR)](https://github.com/musistudio/claude-code-router)
helped establish the broader Claude Code routing space. CCR is a full,
self-contained routing and UI-based management system spanning providers,
profiles, routing rules, and more. Claude OpenRouter is intentionally narrower:
it is a simple connection from Claude Code to OpenRouter, installs with one
small Bash command (or `uv`/`uvx`), and does not aim to cover CCR's full
management spectrum.
