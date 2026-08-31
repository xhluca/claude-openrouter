from __future__ import annotations

import json
import subprocess

from claude_openrouter import check
from claude_openrouter.check import PROBE_MARKER, parse_probe_result


def _event(value: dict[str, object]) -> str:
    return json.dumps(value)


def test_parse_probe_result_requires_a_completed_tool_round_trip() -> None:
    output = "\n".join(
        [
            _event(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "tool_use", "name": "Glob", "id": "tool-1"}
                        ]
                    },
                }
            ),
            _event(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "tool-1",
                                "content": "clor-tool-probe.txt",
                            }
                        ]
                    },
                }
            ),
            _event(
                {
                    "type": "result",
                    "result": PROBE_MARKER,
                    "total_cost_usd": 0.001,
                }
            ),
        ]
    )

    result = parse_probe_result(output, "", returncode=0)

    assert result.passed
    assert result.tool_called
    assert result.tool_completed
    assert result.acknowledged_result
    assert result.total_cost_usd == 0.001


def test_parse_probe_result_rejects_uncompleted_or_unacknowledged_calls() -> None:
    output = "\n".join(
        [
            _event(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "tool_use", "name": "Glob", "id": "tool-1"}
                        ]
                    },
                }
            ),
            _event({"type": "result", "result": "not the marker"}),
        ]
    )

    result = parse_probe_result(output, "", returncode=0)

    assert result.tool_called
    assert not result.tool_completed
    assert not result.acknowledged_result
    assert not result.passed


def test_parse_probe_result_can_use_claude_debug_dispatch_events() -> None:
    debug = "\n".join(
        [
            "tool_dispatch_start tool=Glob id=tool-2",
            "tool_dispatch_end tool=Glob id=tool-2 outcome=ok",
        ]
    )
    output = _event({"type": "result", "result": PROBE_MARKER})

    result = parse_probe_result(output, debug, returncode=0)

    assert result.passed


def test_probe_never_consumes_the_calling_shell_input(monkeypatch) -> None:
    captured = {}

    class FakeServer:
        server_port = 12345

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def serve_forever(self) -> None:
            pass

        def shutdown(self) -> None:
            pass

        def server_close(self) -> None:
            pass

    def run(_command, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(check, "HybridRouterServer", FakeServer)
    monkeypatch.setattr(check, "find_claude", lambda: "/usr/bin/claude")
    monkeypatch.setattr(check.subprocess, "run", run)

    check.probe_model({"id": "vendor/model"})

    assert captured["stdin"] is subprocess.DEVNULL
