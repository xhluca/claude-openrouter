<h1 align="center">Claude OpenRouter</h1>

<p align="center"><strong>Put your OpenRouter favorites directly in Claude Code's <code>/model</code> picker.</strong></p>

<p align="center">
  <a href="https://pypi.org/project/claude-openrouter/"><img src="https://img.shields.io/pypi/v/claude-openrouter?style=flat-square&color=d97757&label=PyPI" alt="PyPI version"></a>
  <a href="https://pypi.org/project/claude-openrouter/"><img src="https://img.shields.io/pypi/pyversions/claude-openrouter?style=flat-square" alt="Python versions"></a>
  <a href="https://github.com/xhluca/claude-openrouter/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-8dbdff?style=flat-square" alt="MIT license"></a>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/xhluca/claude-openrouter/main/docs/assets/demo.gif" alt="Installing Claude OpenRouter, searching OpenRouter models, and adding favorites to Claude Code" width="860">
</p>

Claude OpenRouter is a small, dependency-free CLI. It indexes OpenRouter's live
model catalog, gives you a searchable multi-select picker, and writes your
favorites into Claude Code's native `/model` menu. There is no local proxy,
background service, or replacement harness.

## Install

```bash
curl -LsSf https://raw.githubusercontent.com/xhluca/claude-openrouter/main/install.sh | sh
```

The installer prompts for your OpenRouter key without echoing it, fetches the
current catalog, and opens the model picker immediately. It installs for the
current user on Linux or macOS.

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
replaces the saved favorite set. In the picker, type a search and press Enter,
use the arrow keys, press Enter or Space to select and deselect, `/` to search
again, and `s` to save.

Restart Claude Code, then switch normally:

```text
> /model
```

The selected rows are Claude Code's native picker rows, and requests use the
OpenRouter key stored by this tool.

## Commands

| Command | Purpose |
| --- | --- |
| `clor index` | Fetch and cache the current OpenRouter model catalog |
| `clor fetch` | Alias for `index` |
| `clor search QUERY...` | Refresh, then search names, IDs, and descriptions |
| `clor setup` | Run the install-time key and model setup again |
| `clor select [MODEL]` | Replace `/model` favorites exactly |
| `clor config` | Replace and validate the stored OpenRouter key |
| `clor reset` | Restore the original Claude settings and delete tool data |
| `clor uninstall` | Reset the integration and remove a curl/uv installation |

Every catalog command also accepts `--json` where useful. API keys are never
accepted as command-line arguments; automation can pipe one to `setup
--key-stdin` or `config --key-stdin`.

## What it changes

```text
OpenRouter API → local model index → selected favorites → Claude Code /model
```

- Stores the key at `~/.config/claude-openrouter/credential` with mode `0600`.
- Adds a tiny `apiKeyHelper` to Claude Code, so the key is not copied into
  `~/.claude/settings.json`.
- Sets Claude Code's API base URL to OpenRouter's native Anthropic-compatible
  endpoint and clears higher-precedence Anthropic key/token variables inside
  Claude Code so the helper is selected.
- Writes selected models through Claude Code's user-level `modelPicker` setting.
- Backs up only the settings fields it owns and preserves unrelated settings.

Claude Code is optimized for Anthropic models. OpenRouter can accept other model
IDs through its Anthropic-compatible endpoint, but models differ in tool use,
thinking blocks, context handling, and Claude Code compatibility. Prefer models
that OpenRouter documents as suitable for agentic tool use.

## Reset and uninstall

Restore the pre-install Claude settings while keeping the command:

```bash
clor reset
```

Restore settings, remove the credential and index, and uninstall a curl/uv tool
installation:

```bash
clor uninstall
```

## Demo

The animation above is rendered from a real asciinema capture of the installed
CLI and the native Claude Code TUI: hidden key entry, live catalog indexing,
glob search, interactive multi-selection, and the resulting `/model` rows. It
uses a deliberately fake credential only to read OpenRouter's public catalog;
it makes no inference request. The prompt is not echoed, and the scripts reject
any cast containing a key-shaped string before publishing it. The only
post-processing replaces the randomized temporary home path with `~`.

[Watch the MP4](https://github.com/xhluca/claude-openrouter/raw/main/docs/assets/demo.mp4)
or [reproduce the capture](scripts/render-demo.sh).

## Development

```bash
git clone https://github.com/xhluca/claude-openrouter.git
cd claude-openrouter
uv run --with pytest pytest
uv run --with ruff ruff check .
```

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
