from __future__ import annotations

import json

from claude_openrouter.agents import (
    MANAGED_MARKER,
    agent_name,
    remove_managed_agents,
    rewrite_agent_input,
    sync_managed_agents,
)
from claude_openrouter.paths import agent_manifest_path, claude_agents_dir


def test_managed_agents_expose_each_exact_openrouter_favorite(
    isolated_home, sample_models
) -> None:
    selected = sample_models[2:]

    routes = sync_managed_agents(selected)

    assert routes == {
        agent_name(model["id"]): f"clor/openrouter/{model['id']}" for model in selected
    }
    manifest = json.loads(agent_manifest_path().read_text())
    assert set(manifest["agents"]) == set(routes)
    for name, route in routes.items():
        path = claude_agents_dir() / f"{name}.md"
        document = path.read_text()
        assert MANAGED_MARKER in document
        assert f"model: {json.dumps(route)}" in document
        assert "Do not pass the Agent model parameter" in document


def test_agent_hook_removes_native_alias_override_only_for_managed_agent(
    isolated_home, sample_models
) -> None:
    selected = sample_models[2:]
    routes = sync_managed_agents(selected)
    managed = next(iter(routes))
    original = {
        "description": "delegate",
        "prompt": "check it",
        "subagent_type": managed,
        "model": "sonnet",
        "run_in_background": True,
    }

    result = rewrite_agent_input({"tool_name": "Agent", "tool_input": original})

    assert result is not None
    output = result["hookSpecificOutput"]
    assert output["permissionDecision"] == "allow"
    assert output["updatedInput"] == {
        key: value for key, value in original.items() if key != "model"
    }
    assert rewrite_agent_input(
        {
            "tool_name": "Agent",
            "tool_input": {**original, "subagent_type": "general-purpose"},
        }
    ) is None
    assert rewrite_agent_input({"tool_name": "Read", "tool_input": original}) is None


def test_reselection_replaces_only_clor_owned_agent_files(isolated_home, sample_models) -> None:
    sync_managed_agents(sample_models[2:])
    unrelated = claude_agents_dir() / "user-agent.md"
    unrelated.write_text("user owned")

    sync_managed_agents(sample_models[3:])

    assert unrelated.read_text() == "user owned"
    assert not (claude_agents_dir() / f"{agent_name(sample_models[2]['id'])}.md").exists()
    assert (claude_agents_dir() / f"{agent_name(sample_models[3]['id'])}.md").exists()

    remove_managed_agents()
    assert unrelated.exists()
    assert not agent_manifest_path().exists()
