"""Fail-closed local router for native Claude and OpenRouter models."""

from __future__ import annotations

import http.client
import json
import ssl
from contextlib import suppress
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .anthropic import read_anthropic_credential
from .models import OPENROUTER_MODEL_PREFIX, hybrid_openrouter_allowed, original_model
from .openrouter import read_credential
from .paths import router_status_path, router_token_path
from .settings import favorite_ids, load_preferences
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


def route_payload(body: bytes, favorites: set[str]) -> tuple[str, str, bytes]:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("request body must be valid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("model"), str):
        raise ValueError("request body must contain a string model")
    route, upstream_model = classify_model(payload["model"], favorites)
    if route == "openrouter":
        payload["model"] = upstream_model
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
                self._json_response(401, {"error": {"message": "invalid local router token"}})
                return
            self._json_response(200, {"status": "ok", "mode": "hybrid"})
            return
        self._json_response(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path not in ALLOWED_PATHS:
            self._json_response(404, {"error": {"message": "unsupported endpoint"}})
            return
        if self.headers.get(LOCAL_TOKEN_HEADER) != self.router.local_token:
            self._json_response(401, {"error": {"message": "invalid local router token"}})
            return
        content_length = self.headers.get("Content-Length")
        try:
            length = int(content_length or "")
        except ValueError:
            self._json_response(411, {"error": {"message": "Content-Length is required"}})
            return
        if length < 0 or length > MAX_BODY_BYTES:
            self._json_response(413, {"error": {"message": "request body is too large"}})
            return
        body = self.rfile.read(length)
        try:
            route, model, body = route_payload(body, self.router.favorites)
            upstream = (
                self.router.openrouter_upstream
                if route == "openrouter"
                else self.router.anthropic_upstream
            )
            headers = self._upstream_headers(route, len(body))
            self._forward(upstream, self.path, headers, body)
            self._record_status(route, model, None)
        except ValueError as exc:
            self._json_response(400, {"error": {"message": str(exc)}})
            self._record_status("rejected", "unknown", str(exc))
        except (OSError, RuntimeError, http.client.HTTPException) as exc:
            self._json_response(502, {"error": {"message": f"routing failed: {exc}"}})
            self._record_status("error", "unknown", str(exc))

    def _upstream_headers(self, route: str, content_length: int) -> dict[str, str]:
        removed = HOP_BY_HOP | {
            "host",
            "content-length",
            "accept-encoding",
            "authorization",
            "x-api-key",
            LOCAL_TOKEN_HEADER.casefold(),
        }
        headers = {
            key: value for key, value in self.headers.items() if key.casefold() not in removed
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
        self, upstream: str, request_path: str, headers: dict[str, str], body: bytes
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
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                if key.casefold() not in HOP_BY_HOP | {"content-length", "server", "date"}:
                    self.send_header(key, value)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            # ``read`` may wait for the full requested size or EOF, which turns
            # SSE token streams into one buffered completion. ``read1`` makes
            # at most one underlying read and returns available bytes promptly.
            while chunk := response.read1(64 * 1024):
                self.wfile.write(f"{len(chunk):X}\r\n".encode())
                self.wfile.write(chunk)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            connection.close()

    def _record_status(self, route: str, model: str, error: str | None) -> None:
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


class HybridRouterServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        local_token: str,
        favorites: set[str],
        anthropic_auth: str,
        anthropic_upstream: str = ANTHROPIC_UPSTREAM,
        openrouter_upstream: str = OPENROUTER_UPSTREAM,
    ) -> None:
        if anthropic_auth not in {"max", "api"}:
            raise ValueError("Anthropic authentication must be max or api")
        self.local_token = local_token
        self.favorites = favorites
        self.anthropic_auth = anthropic_auth
        self.anthropic_upstream = anthropic_upstream
        self.openrouter_upstream = openrouter_upstream
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
    server = HybridRouterServer(
        (host, port),
        local_token=read_router_token(),
        favorites=set(favorite_ids()),
        anthropic_auth=auth,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
