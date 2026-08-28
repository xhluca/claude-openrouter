from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from claude_openrouter.openrouter import write_credential
from claude_openrouter.paths import anthropic_credential_path
from claude_openrouter.proxy import (
    LOCAL_TOKEN_HEADER,
    HybridRouterServer,
    classify_model,
)
from claude_openrouter.storage import atomic_write_text

OPENROUTER_KEY = "sk-or-v1-this-is-a-fake-test-key"
ANTHROPIC_KEY = "sk-ant-this-is-a-fake-test-key"
LOCAL_TOKEN = "local-router-test-token"
GLM = "z-ai/glm-5.3-flash"
STREAM_FIRST = b"data: first\n\n"
STREAM_SECOND = b"data: second\n\n"


class RecordingUpstream(BaseHTTPRequestHandler):
    requests: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers["Content-Length"])
        body = self.rfile.read(length)
        self.requests.append(
            {
                "path": self.path,
                "headers": {key.casefold(): value for key, value in self.headers.items()},
                "body": json.loads(body),
            }
        )
        response = json.dumps({"type": "message", "model": json.loads(body)["model"]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, *_args) -> None:
        return


class StreamingUpstream(BaseHTTPRequestHandler):
    first_written = threading.Event()
    release_second = threading.Event()

    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(STREAM_FIRST) + len(STREAM_SECOND)))
        self.end_headers()
        self.wfile.write(STREAM_FIRST)
        self.wfile.flush()
        self.first_written.set()
        self.release_second.wait(timeout=2)
        self.wfile.write(STREAM_SECOND)
        self.wfile.flush()

    def log_message(self, *_args) -> None:
        return


@pytest.fixture
def routing_servers(isolated_home):
    write_credential(OPENROUTER_KEY)
    atomic_write_text(anthropic_credential_path(), f"{ANTHROPIC_KEY}\n", 0o600)
    RecordingUpstream.requests = []
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), RecordingUpstream)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    base = f"http://127.0.0.1:{upstream.server_port}"
    router = HybridRouterServer(
        ("127.0.0.1", 0),
        local_token=LOCAL_TOKEN,
        favorites={GLM},
        anthropic_auth="max",
        anthropic_upstream=f"{base}/anthropic",
        openrouter_upstream=f"{base}/openrouter",
    )
    router_thread = threading.Thread(target=router.serve_forever, daemon=True)
    router_thread.start()
    try:
        yield router
    finally:
        router.shutdown()
        router.server_close()
        router_thread.join(timeout=2)
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=2)


def request(router, model: str, *, authorization: str = "Bearer max-oauth"):
    body = json.dumps({"model": model, "max_tokens": 1, "messages": []}).encode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": authorization,
        "X-Api-Key": "must-not-leak",
        LOCAL_TOKEN_HEADER: LOCAL_TOKEN,
    }
    return urllib.request.urlopen(
        urllib.request.Request(
            f"http://127.0.0.1:{router.server_port}/v1/messages",
            data=body,
            headers=headers,
            method="POST",
        ),
        timeout=3,
    )


def test_openrouter_route_strips_cross_provider_credentials(routing_servers) -> None:
    with request(routing_servers, f"clor/openrouter/{GLM}") as response:
        assert response.status == 200
    captured = RecordingUpstream.requests[-1]
    assert captured["path"] == "/openrouter/v1/messages"
    assert captured["body"]["model"] == GLM  # type: ignore[index]
    headers = captured["headers"]
    assert headers["authorization"] == f"Bearer {OPENROUTER_KEY}"  # type: ignore[index]
    assert "x-api-key" not in headers
    assert LOCAL_TOKEN_HEADER.casefold() not in headers
    assert "max-oauth" not in json.dumps(captured)


def test_openrouter_stream_is_forwarded_before_upstream_finishes(isolated_home) -> None:
    write_credential(OPENROUTER_KEY)
    StreamingUpstream.first_written = threading.Event()
    StreamingUpstream.release_second = threading.Event()
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), StreamingUpstream)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    router = HybridRouterServer(
        ("127.0.0.1", 0),
        local_token=LOCAL_TOKEN,
        favorites={GLM},
        anthropic_auth="max",
        openrouter_upstream=f"http://127.0.0.1:{upstream.server_port}",
    )
    router_thread = threading.Thread(target=router.serve_forever, daemon=True)
    router_thread.start()
    received = threading.Event()
    result: list[bytes] = []

    def consume() -> None:
        with request(router, f"clor/openrouter/{GLM}") as response:
            result.append(response.read(len(STREAM_FIRST)))
            received.set()
            result.append(response.read())

    client = threading.Thread(target=consume, daemon=True)
    client.start()
    try:
        assert StreamingUpstream.first_written.wait(timeout=1)
        assert received.wait(timeout=0.5), "router buffered the first streaming event"
    finally:
        StreamingUpstream.release_second.set()
        client.join(timeout=2)
        router.shutdown()
        router.server_close()
        router_thread.join(timeout=2)
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=2)
    assert result == [STREAM_FIRST, STREAM_SECOND]


def test_native_route_preserves_oauth_and_never_uses_openrouter_key(routing_servers) -> None:
    with request(routing_servers, "claude-sonnet-4-6") as response:
        assert response.status == 200
    captured = RecordingUpstream.requests[-1]
    assert captured["path"] == "/anthropic/v1/messages"
    assert captured["body"]["model"] == "claude-sonnet-4-6"  # type: ignore[index]
    headers = captured["headers"]
    assert headers["authorization"] == "Bearer max-oauth"  # type: ignore[index]
    assert "x-api-key" not in headers
    assert OPENROUTER_KEY not in json.dumps(captured)


def test_unknown_and_unfavorited_models_fail_closed(routing_servers) -> None:
    for model in ("google/gemini", "clor/openrouter/not/selected"):
        with pytest.raises(urllib.error.HTTPError) as rejected:
            request(routing_servers, model)
        assert rejected.value.code == 400
    assert RecordingUpstream.requests == []


def test_local_fallback_token_cannot_be_used_as_max_oauth(routing_servers) -> None:
    with pytest.raises(urllib.error.HTTPError) as rejected:
        request(routing_servers, "claude-opus-5", authorization=f"Bearer {LOCAL_TOKEN}")
    assert rejected.value.code == 502
    assert RecordingUpstream.requests == []


def test_anthropic_api_mode_injects_only_anthropic_key(routing_servers) -> None:
    routing_servers.anthropic_auth = "api"
    with request(routing_servers, "claude-opus-5") as response:
        assert response.status == 200
    captured = RecordingUpstream.requests[-1]
    headers = captured["headers"]
    assert headers["x-api-key"] == ANTHROPIC_KEY  # type: ignore[index]
    assert "authorization" not in headers
    assert OPENROUTER_KEY not in json.dumps(captured)


def test_healthcheck_requires_local_token(routing_servers) -> None:
    url = f"http://127.0.0.1:{routing_servers.server_port}/healthz"
    with pytest.raises(urllib.error.HTTPError) as rejected:
        urllib.request.urlopen(url, timeout=3)
    assert rejected.value.code == 401
    with urllib.request.urlopen(
        urllib.request.Request(url, headers={LOCAL_TOKEN_HEADER: LOCAL_TOKEN}), timeout=3
    ) as response:
        assert response.status == 200


def test_model_classification_is_explicit() -> None:
    assert classify_model("claude-opus-5", {GLM}) == ("anthropic", "claude-opus-5")
    assert classify_model(f"clor/openrouter/{GLM}", {GLM}) == ("openrouter", GLM)
    with pytest.raises(ValueError, match="no trusted route"):
        classify_model(GLM, {GLM})
    with pytest.raises(ValueError, match="blocked on the OpenRouter route"):
        classify_model("clor/openrouter/anthropic/claude-opus-5", {"anthropic/claude-opus-5"})
