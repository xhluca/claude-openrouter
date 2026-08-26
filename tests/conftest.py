from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv("XDG_BIN_HOME", str(home / ".local" / "bin"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    return home


@pytest.fixture
def sample_models() -> list[dict[str, Any]]:
    return [
        {
            "id": "anthropic/claude-sonnet-4.6",
            "name": "Claude Sonnet 4.6",
            "description": "Fast agentic coding model",
            "context_length": 200_000,
            "pricing": {"prompt": "0.000003", "completion": "0.000015"},
        },
        {
            "id": "anthropic/claude-opus-4.6",
            "name": "Claude Opus 4.6",
            "description": "Deep reasoning model",
            "context_length": 200_000,
            "pricing": {"prompt": "0.000005", "completion": "0.000025"},
        },
        {
            "id": "google/gemini-3.1-pro-preview",
            "name": "Gemini 3.1 Pro Preview",
            "description": "Multimodal reasoning and tools",
            "context_length": 1_000_000,
            "pricing": {"prompt": "0.000002", "completion": "0.000012"},
        },
        {
            "id": "qwen/qwen3-coder",
            "name": "Qwen3 Coder",
            "description": "Coding model",
            "context_length": 262_144,
            "pricing": {"prompt": "0", "completion": "0"},
        },
    ]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")

