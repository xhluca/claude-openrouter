"""Live Claude Code tool-compatibility probe for an OpenRouter model."""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import tempfile
import threading
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .launcher import find_claude
from .models import catalog_input_modalities, namespaced_model
from .proxy import LOCAL_TOKEN_HEADER, HybridRouterServer, router_base_url

PROBE_MARKER = "CLOR_TOOL_CHECK_OK"
PROBE_FILENAME = "clor-tool-probe.txt"
PROBE_INPUT_TOKEN_ESTIMATE = 15_000
PROBE_OUTPUT_TOKEN_ESTIMATE = 500
PROBE_INPUT_TOKEN_PLANNING_MAX = 500_000
PROBE_OUTPUT_TOKEN_PLANNING_MAX = 2_000


@dataclass(frozen=True)
class ToolProbeResult:
    tool_called: bool
    tool_completed: bool
    acknowledged_result: bool
    returncode: int
    total_cost_usd: float | None
    final_text: str
    diagnostic: str

    @property
    def passed(self) -> bool:
        return (
            self.returncode == 0
            and self.tool_called
            and self.tool_completed
            and self.acknowledged_result
        )


def estimate_probe_cost(
    model: dict[str, Any],
    *,
    input_tokens: int = PROBE_INPUT_TOKEN_ESTIMATE,
    output_tokens: int = PROBE_OUTPUT_TOKEN_ESTIMATE,
) -> Decimal | None:
    """Estimate a probe from catalog rates and the expected Claude Code context."""
    pricing = model.get("pricing")
    if not isinstance(pricing, dict):
        return None
    try:
        prompt = Decimal(str(pricing["prompt"]))
        completion = Decimal(str(pricing["completion"]))
    except (InvalidOperation, KeyError, TypeError, ValueError):
        return None
    if not prompt.is_finite() or not completion.is_finite() or prompt < 0 or completion < 0:
        return None
    return (
        prompt * input_tokens
        + completion * output_tokens
    )


def _stream_events(output: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def parse_probe_result(
    output: str, debug_log: str, *, returncode: int, stderr: str = ""
) -> ToolProbeResult:
    events = _stream_events(output)
    tool_ids: set[str] = set()
    completed_ids: set[str] = set()
    final_text = ""
    total_cost: float | None = None
    for event in events:
        message = event.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use" and block.get("name") == "Glob":
                        tool_id = block.get("id")
                        if isinstance(tool_id, str):
                            tool_ids.add(tool_id)
                    if block.get("type") == "tool_result":
                        tool_id = block.get("tool_use_id")
                        if isinstance(tool_id, str) and not block.get("is_error", False):
                            completed_ids.add(tool_id)
        if event.get("type") == "result":
            result = event.get("result")
            if isinstance(result, str):
                final_text = result
            cost = event.get("total_cost_usd")
            if isinstance(cost, int | float):
                total_cost = float(cost)

    debug_called = re.search(r"tool_dispatch_start\s+tool=Glob\b", debug_log) is not None
    debug_completed = (
        re.search(r"tool_dispatch_end\s+tool=Glob\b[^\n]*\boutcome=ok\b", debug_log)
        is not None
    )
    tool_called = bool(tool_ids) or debug_called
    tool_completed = bool(tool_ids & completed_ids) or debug_completed
    diagnostic = stderr.strip()[-1000:]
    return ToolProbeResult(
        tool_called=tool_called,
        tool_completed=tool_completed,
        acknowledged_result=PROBE_MARKER in final_text,
        returncode=returncode,
        total_cost_usd=total_cost,
        final_text=final_text,
        diagnostic=diagnostic,
    )


def probe_model(model: dict[str, Any], *, timeout: int = 180) -> ToolProbeResult:
    """Run an isolated, billable Glob round-trip through real Claude Code."""
    model_id = str(model["id"])
    token = secrets.token_urlsafe(32)
    server = HybridRouterServer(
        ("127.0.0.1", 0),
        local_token=token,
        favorites={model_id},
        anthropic_auth="max",
        model_modalities=catalog_input_modalities([model]),
        record_status=False,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="clor-tool-check-") as directory:
            workdir = Path(directory)
            (workdir / PROBE_FILENAME).write_text("tool round-trip probe\n", encoding="utf-8")
            debug_path = workdir / "claude-debug.log"
            environment = dict(os.environ)
            for name in (
                "CLAUDE_CODE_USE_BEDROCK",
                "CLAUDE_CODE_USE_FOUNDRY",
                "CLAUDE_CODE_USE_VERTEX",
            ):
                environment.pop(name, None)
            environment.update(
                {
                    "ANTHROPIC_BASE_URL": router_base_url(server.server_port),
                    "ANTHROPIC_API_KEY": "",
                    "ANTHROPIC_AUTH_TOKEN": token,
                    "ANTHROPIC_CUSTOM_HEADERS": f"{LOCAL_TOKEN_HEADER}: {token}",
                    "ENABLE_TOOL_SEARCH": "false",
                }
            )
            command = [
                find_claude(),
                "-p",
                "--model",
                namespaced_model(model_id),
                "--name",
                "clor-tool-check",
                "--setting-sources",
                "project,local",
                "--tools",
                "Glob",
                "--permission-mode",
                "dontAsk",
                "--no-session-persistence",
                "--output-format",
                "stream-json",
                "--verbose",
                "--debug-file",
                str(debug_path),
                (
                    f"Use the Glob tool exactly once to find {PROBE_FILENAME} in the current "
                    f"directory. Only after reading the tool result, reply exactly {PROBE_MARKER}."
                ),
            ]
            try:
                completed = subprocess.run(
                    command,
                    cwd=workdir,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired as exc:
                output = exc.stdout if isinstance(exc.stdout, str) else ""
                stderr = exc.stderr if isinstance(exc.stderr, str) else ""
                debug_log = debug_path.read_text(errors="replace") if debug_path.exists() else ""
                result = parse_probe_result(output, debug_log, returncode=124, stderr=stderr)
                return replace(result, diagnostic="tool check timed out")
            debug_log = debug_path.read_text(errors="replace") if debug_path.exists() else ""
            return parse_probe_result(
                completed.stdout,
                debug_log,
                returncode=completed.returncode,
                stderr=completed.stderr,
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
