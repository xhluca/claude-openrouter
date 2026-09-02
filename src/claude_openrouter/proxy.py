"""Fail-closed local router for native Claude and OpenRouter models."""

from __future__ import annotations

import http.client
import json
import ssl
from contextlib import suppress
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from typing import Any
from urllib.parse import urlsplit

from .anthropic import read_anthropic_credential
from .models import (
    OPENROUTER_MODEL_PREFIX,
    catalog_input_modalities,
    exact_models,
    hybrid_openrouter_allowed,
    original_model,
)
from .openrouter import load_catalog, read_credential
from .paths import router_status_path, router_token_path
from .settings import favorite_ids, load_preferences, refresh_managed_subagents
from .storage import atomic_write_json

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9417
ANTHROPIC_UPSTREAM = "https://api.anthropic.com"
OPENROUTER_UPSTREAM = "https://openrouter.ai/api"
LOCAL_TOKEN_HEADER = "X-Claude-OpenRouter-Token"
MAX_BODY_BYTES = 128 * 1024 * 1024
ALLOWED_PATHS = {"/v1/messages", "/v1/messages/count_tokens"}
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

CAPABILITY_ERROR = "unsupported_input_modality"
GEMINI_MODEL_PREFIX = "google/gemini"


def _vision_hint(favorites: set[str], model_modalities: dict[str, frozenset[str]]) -> str:
    vision_favorites = sorted(
        model_id for model_id in favorites if "image" in model_modalities.get(model_id, frozenset())
    )
    if not vision_favorites:
        return "Use /model to switch to a vision-capable model, then retry."
    examples = ", ".join(vision_favorites[:3])
    label = "favorite" if len(vision_favorites) == 1 else "favorites"
    return f"Use /model to switch to a vision-capable {label} such as {examples}, then retry."


def _capability_notice(model: str, vision_hint: str) -> str:
    return (
        "Claude OpenRouter capability notice: the selected model "
        f"{model} is text-only; OpenRouter's catalog does not list image as an input "
        "modality. You cannot inspect image pixels with this model. Do not claim that "
        "you viewed an image or repeatedly call a tool to read one. If the user asks "
        f"you to inspect an image, explain this limitation. {vision_hint}"
    )


def _tool_capability_error(model: str, vision_hint: str) -> str:
    return (
        f"ToolError[{CAPABILITY_ERROR}]: the Read tool returned image content, but the "
        f"selected OpenRouter model {model} is text-only. The image was not sent to the "
        "model. Do not retry reading this image with the current model. Explain that this "
        f"model cannot inspect images. {vision_hint}"
    )


def _input_capability_error(model: str, vision_hint: str) -> str:
    return (
        f"InputError[{CAPABILITY_ERROR}]: an image was supplied, but the selected "
        f"OpenRouter model {model} is text-only. The image was not sent to the model. "
        f"Explain that this model cannot inspect images. {vision_hint}"
    )


def _contains_image(content: Any) -> bool:
    if isinstance(content, list):
        return any(_contains_image(item) for item in content)
    if isinstance(content, dict):
        if content.get("type") == "image":
            return True
        return any(_contains_image(value) for value in content.values())
    return False


def _replace_unsupported_images(payload: dict[str, Any], model: str, vision_hint: str) -> int:
    """Turn image results into errors that a text-only model can act on."""
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return 0
    error_text = _tool_capability_error(model, vision_hint)
    input_error_text = _input_capability_error(model, vision_hint)
    replaced = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        new_content: list[Any] = []
        for block in content:
            if not isinstance(block, dict):
                new_content.append(block)
                continue
            if block.get("type") == "tool_result" and _contains_image(block.get("content")):
                replacement = dict(block)
                replacement["content"] = [{"type": "text", "text": error_text}]
                replacement["is_error"] = True
                new_content.append(replacement)
                replaced += 1
            elif block.get("type") == "image":
                new_content.append({"type": "text", "text": input_error_text})
                replaced += 1
            else:
                new_content.append(block)
        message["content"] = new_content
    return replaced


def _append_system_notice(payload: dict[str, Any], notice: str) -> None:
    system = payload.get("system")
    if isinstance(system, str):
        if notice not in system:
            payload["system"] = f"{system}\n\n{notice}"
    elif isinstance(system, list):
        if not any(isinstance(block, dict) and block.get("text") == notice for block in system):
            system.append({"type": "text", "text": notice})
    elif system is None:
        payload["system"] = notice


def _repair_itemless_arrays(value: Any) -> int:
    """Make valid open-ended JSON arrays acceptable to Gemini's stricter schema."""
    if isinstance(value, list):
        return sum(_repair_itemless_arrays(item) for item in value)
    if not isinstance(value, dict):
        return 0
    repaired = 0
    if str(value.get("type", "")).casefold() == "array" and "items" not in value:
        # JSON Schema permits an omitted ``items`` (unconstrained elements), but
        # Gemini's function-declaration Schema proto requires an element schema.
        # String is the least surprising representation for CLI/MCP arguments.
        value["items"] = {"type": "string"}
        repaired += 1
    return repaired + sum(_repair_itemless_arrays(item) for item in value.values())


def _repair_gemini_tool_schemas(payload: dict[str, Any]) -> int:
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return 0
    return _repair_itemless_arrays(tools)


def _remove_gemini_adaptive_thinking(payload: dict[str, Any]) -> bool:
    """Drop Claude thinking controls that can yield empty Gemini turns."""
    thinking = payload.get("thinking")
    if not isinstance(thinking, dict) or thinking.get("type") != "adaptive":
        return False
    payload.pop("thinking")
    output_config = payload.get("output_config")
    if isinstance(output_config, dict) and "effort" in output_config:
        output_config.pop("effort")
        if not output_config:
            payload.pop("output_config")
    return True


def _sse_event_json(event: bytes) -> dict[str, Any] | None:
    data: list[bytes] = []
    for line in event.replace(b"\r\n", b"\n").splitlines():
        if line == b"data":
            data.append(b"")
        elif line.startswith(b"data:"):
            data.append(line[5:].lstrip(b" "))
    if not data or data == [b"[DONE]"]:
        return None
    try:
        value = json.loads(b"\n".join(data))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _filter_gemini_sse_event(event: bytes, thinking_indexes: set[int]) -> bytes:
    """Hide Gemini thinking blocks that Claude Code cannot parse reliably."""
    value = _sse_event_json(event)
    if value is None:
        return event
    index = value.get("index")
    event_type = value.get("type")
    content_block = value.get("content_block")
    delta = value.get("delta")
    if (
        event_type == "content_block_start"
        and isinstance(index, int)
        and isinstance(content_block, dict)
        and content_block.get("type") in {"thinking", "redacted_thinking"}
    ):
        thinking_indexes.add(index)
        return b""
    if isinstance(index, int) and index in thinking_indexes:
        if event_type == "content_block_stop":
            thinking_indexes.discard(index)
        return b""
    if (
        event_type == "content_block_delta"
        and isinstance(index, int)
        and isinstance(delta, dict)
        and delta.get("type") in {"thinking_delta", "signature_delta"}
    ):
        thinking_indexes.add(index)
        return b""
    return event


def _next_sse_event(buffer: bytes) -> tuple[bytes, bytes] | None:
    endings = [
        (position, separator)
        for separator in (b"\n\n", b"\r\n\r\n")
        if (position := buffer.find(separator)) >= 0
    ]
    if not endings:
        return None
    position, separator = min(endings, key=lambda item: item[0])
    end = position + len(separator)
    return buffer[:end], buffer[end:]


def _remove_gemini_thinking_content(body: bytes) -> bytes:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body
    if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
        return body
    payload["content"] = [
        block
        for block in payload["content"]
        if not isinstance(block, dict)
        or block.get("type") not in {"thinking", "redacted_thinking"}
    ]
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()


def router_base_url(port: int = DEFAULT_PORT) -> str:
    return f"http://{DEFAULT_HOST}:{port}"


def _read_secret(path: str | Path, label: str) -> str:
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise RuntimeError(f"{label} not found at {path}") from exc
    if not value or any(character.isspace() for character in value):
        raise RuntimeError(f"invalid {label} at {path}")
    return value


def read_router_token() -> str:
    return _read_secret(router_token_path(), "router token")


def classify_model(model: str, favorites: set[str]) -> tuple[str, str]:
    """Return ``(route, upstream_model)`` or reject an ambiguous model."""
    openrouter_model = original_model(model)
    if openrouter_model is not None:
        if not hybrid_openrouter_allowed(openrouter_model):
            raise ValueError("Anthropic and automatic models are blocked on the OpenRouter route")
        if openrouter_model not in favorites:
            raise ValueError("OpenRouter model is not in the clor favorites allowlist")
        return "openrouter", openrouter_model
    if model in {"default", "opus", "sonnet", "haiku"} or model.startswith("claude-"):
        return "anthropic", model
    raise ValueError(
        "model has no trusted route; OpenRouter models must use the "
        f"{OPENROUTER_MODEL_PREFIX} namespace"
    )


def route_payload(
    body: bytes,
    favorites: set[str],
    model_modalities: dict[str, frozenset[str]] | None = None,
) -> tuple[str, str, bytes]:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("request body must be valid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("model"), str):
        raise ValueError("request body must contain a string model")
    route, upstream_model = classify_model(payload["model"], favorites)
    if route == "openrouter":
        payload["model"] = upstream_model
        if upstream_model.casefold().startswith(GEMINI_MODEL_PREFIX):
            _repair_gemini_tool_schemas(payload)
            _remove_gemini_adaptive_thinking(payload)
        modalities = (model_modalities or {}).get(upstream_model)
        if modalities is not None and "image" not in modalities:
            vision_hint = _vision_hint(favorites, model_modalities or {})
            _append_system_notice(payload, _capability_notice(upstream_model, vision_hint))
            _replace_unsupported_images(payload, upstream_model, vision_hint)
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    return route, upstream_model, body


def _target(upstream: str) -> tuple[type[http.client.HTTPConnection], str, int | None, str]:
    parsed = urlsplit(upstream)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("invalid configured upstream")
    connection = (
        http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    )
    return connection, parsed.hostname, parsed.port, parsed.path.rstrip("/")


class HybridRouterHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ClaudeOpenRouter"

    @property
    def router(self) -> HybridRouterServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0].rstrip("/") == "/healthz":
            if self.headers.get(LOCAL_TOKEN_HEADER) != self.router.local_token:
                self._error_response(401, "authentication_error", "invalid local router token")
                return
            self._json_response(200, {"status": "ok", "mode": "hybrid"})
            return
        self._error_response(404, "not_found_error", "not found")

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path not in ALLOWED_PATHS:
            self._error_response(404, "not_found_error", "unsupported endpoint")
            return
        if self.headers.get(LOCAL_TOKEN_HEADER) != self.router.local_token:
            self._error_response(401, "authentication_error", "invalid local router token")
            return
        content_length = self.headers.get("Content-Length")
        try:
            length = int(content_length or "")
        except ValueError:
            self._error_response(411, "invalid_request_error", "Content-Length is required")
            return
        if length < 0 or length > MAX_BODY_BYTES:
            self._error_response(413, "request_too_large", "request body is too large")
            return
        body = self.rfile.read(length)
        try:
            route, model, body = route_payload(
                body, self.router.favorites, self.router.model_modalities
            )
            upstream = (
                self.router.openrouter_upstream
                if route == "openrouter"
                else self.router.anthropic_upstream
            )
            headers = self._upstream_headers(route, model, len(body))
            self._forward(
                upstream,
                self.path,
                headers,
                body,
                normalize_gemini=(
                    route == "openrouter"
                    and model.casefold().startswith(GEMINI_MODEL_PREFIX)
                ),
            )
            self._record_status(route, model, None)
        except ValueError as exc:
            self._error_response(400, "invalid_request_error", str(exc))
            self._record_status("rejected", "unknown", str(exc))
        except (OSError, RuntimeError, http.client.HTTPException) as exc:
            self._error_response(502, "api_error", f"routing failed: {exc}")
            self._record_status("error", "unknown", str(exc))

    def _upstream_headers(
        self, route: str, model: str, content_length: int
    ) -> dict[str, str]:
        removed = HOP_BY_HOP | {
            "host",
            "content-length",
            "accept-encoding",
            "authorization",
            "x-api-key",
            LOCAL_TOKEN_HEADER.casefold(),
        }
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.casefold() not in removed
            and not (
                route == "openrouter"
                and model.casefold().startswith(GEMINI_MODEL_PREFIX)
                and key.casefold() == "anthropic-beta"
            )
        }
        headers["Content-Length"] = str(content_length)
        headers["Accept-Encoding"] = "identity"
        headers.setdefault("Content-Type", "application/json")
        if route == "openrouter":
            headers["Authorization"] = f"Bearer {read_credential()}"
            headers["HTTP-Referer"] = "https://github.com/xhluca/claude-openrouter"
            headers["X-Title"] = "Claude OpenRouter"
        elif self.router.anthropic_auth == "api":
            headers["X-Api-Key"] = read_anthropic_credential()
        else:
            authorization = self.headers.get("Authorization", "")
            if not authorization.startswith("Bearer "):
                raise RuntimeError("native Claude OAuth bearer is unavailable")
            if authorization.removeprefix("Bearer ").strip() == self.router.local_token:
                raise RuntimeError("native Claude OAuth bearer is unavailable")
            headers["Authorization"] = authorization
        return headers

    def _forward(
        self,
        upstream: str,
        request_path: str,
        headers: dict[str, str],
        body: bytes,
        *,
        normalize_gemini: bool = False,
    ) -> None:
        connection_type, hostname, port, base_path = _target(upstream)
        kwargs: dict[str, Any] = {"timeout": 600}
        if connection_type is http.client.HTTPSConnection:
            kwargs["context"] = ssl.create_default_context()
        connection = connection_type(hostname, port, **kwargs)
        path = f"{base_path}{request_path}"
        try:
            connection.request("POST", path, body=body, headers=headers)
            response = connection.getresponse()
            content_type = response.getheader("Content-Type", "")
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                if key.casefold() not in HOP_BY_HOP | {"content-length", "server", "date"}:
                    self.send_header(key, value)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            if normalize_gemini and "text/event-stream" in content_type.casefold():
                self._forward_gemini_sse(response)
            elif normalize_gemini:
                self._write_chunk(_remove_gemini_thinking_content(response.read()))
            else:
                # ``read`` may wait for the full requested size or EOF, which turns
                # SSE token streams into one buffered completion. ``read1`` makes
                # at most one underlying read and returns available bytes promptly.
                while chunk := response.read1(64 * 1024):
                    self._write_chunk(chunk)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            connection.close()

    def _write_chunk(self, chunk: bytes) -> None:
        if not chunk:
            return
        self.wfile.write(f"{len(chunk):X}\r\n".encode())
        self.wfile.write(chunk)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def _forward_gemini_sse(self, response: http.client.HTTPResponse) -> None:
        buffer = b""
        thinking_indexes: set[int] = set()
        while chunk := response.read1(64 * 1024):
            buffer += chunk
            while split := _next_sse_event(buffer):
                event, buffer = split
                self._write_chunk(_filter_gemini_sse_event(event, thinking_indexes))
        if buffer:
            self._write_chunk(_filter_gemini_sse_event(buffer, thinking_indexes))

    def _record_status(self, route: str, model: str, error: str | None) -> None:
        if not self.router.record_status:
            return
        status: dict[str, Any] = {
            "version": 1,
            "at": datetime.now(timezone.utc).isoformat(),
            "route": route,
            "model": model,
        }
        if error:
            status["error"] = error[:500]
        with suppress(OSError):
            atomic_write_json(router_status_path(), status)

    def _json_response(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error_response(self, status: int, error_type: str, message: str) -> None:
        self._json_response(
            status,
            {"type": "error", "error": {"type": error_type, "message": message}},
        )


class HybridRouterServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self) -> None:
        """Bind without HTTPServer's unnecessary reverse-DNS lookup.

        ``HTTPServer.server_bind`` calls ``socket.getfqdn`` after binding but
        before listening. That lookup can stall for minutes on macOS when local
        DNS is unavailable, leaving launchd with a bound-but-unhealthy router.
        The router never uses ``server_name``, so the numeric loopback address
        is both sufficient and deterministic.
        """
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)

    def __init__(
        self,
        address: tuple[str, int],
        *,
        local_token: str,
        favorites: set[str],
        anthropic_auth: str,
        anthropic_upstream: str = ANTHROPIC_UPSTREAM,
        openrouter_upstream: str = OPENROUTER_UPSTREAM,
        model_modalities: dict[str, frozenset[str]] | None = None,
        record_status: bool = True,
    ) -> None:
        if anthropic_auth not in {"max", "api"}:
            raise ValueError("Anthropic authentication must be max or api")
        self.local_token = local_token
        self.favorites = favorites
        self.anthropic_auth = anthropic_auth
        self.anthropic_upstream = anthropic_upstream
        self.openrouter_upstream = openrouter_upstream
        self.model_modalities = model_modalities or {}
        self.record_status = record_status
        super().__init__(address, HybridRouterHandler)


def run_router(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("the hybrid router may only bind to a loopback address")
    if not 1 <= port <= 65535:
        raise ValueError("router port must be between 1 and 65535")
    preferences = load_preferences()
    auth = preferences.get("anthropic_auth", "max")
    if not isinstance(auth, str):
        raise RuntimeError("invalid anthropic_auth preference")
    favorites = favorite_ids()
    catalog = load_catalog()
    # The newly installed router is the first new-version process started by
    # ``clor update``. Refreshing here upgrades existing 0.4.x installations
    # without requiring users to rerun setup or select.
    refresh_managed_subagents(exact_models(catalog, favorites))
    server = HybridRouterServer(
        (host, port),
        local_token=read_router_token(),
        favorites=set(favorites),
        anthropic_auth=auth,
        model_modalities=catalog_input_modalities(catalog),
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
