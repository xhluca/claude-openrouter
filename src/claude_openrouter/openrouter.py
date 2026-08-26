"""OpenRouter API access and local model indexing."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from .paths import catalog_path, credential_path
from .storage import atomic_write_json, atomic_write_text, read_json_object

DEFAULT_API_BASE = "https://openrouter.ai/api/v1"
KEY_PATTERN = re.compile(r"^sk-or-[^\s]{10,}$")


def api_base() -> str:
    return os.environ.get("CLAUDE_OPENROUTER_API_BASE", DEFAULT_API_BASE).rstrip("/")


def validate_key_shape(key: str) -> None:
    if not KEY_PATTERN.fullmatch(key):
        raise ValueError("the OpenRouter key has an unexpected format (expected sk-or-...)")


def read_credential() -> str:
    path = credential_path()
    try:
        key = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise RuntimeError(f"OpenRouter credential not found at {path}; run setup") from exc
    validate_key_shape(key)
    return key


def write_credential(key: str) -> None:
    validate_key_shape(key)
    atomic_write_text(credential_path(), f"{key}\n", 0o600)


def _error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read(8192))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ""
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return f": {error['message'][:500]}"
    if isinstance(error, str):
        return f": {error[:500]}"
    return ""


def api_json(path: str, key: str) -> dict[str, Any]:
    validate_key_shape(key)
    request = urllib.request.Request(
        f"{api_base()}/{path.lstrip('/')}",
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "claude-openrouter/0.1",
            "X-Title": "Claude OpenRouter",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"OpenRouter rejected the request (HTTP {exc.code}){_error_detail(exc)}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"could not reach OpenRouter: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("OpenRouter returned an invalid JSON response")
    return payload


def validate_key(key: str) -> None:
    payload = api_json("key", key)
    if not isinstance(payload.get("data"), dict):
        raise RuntimeError("OpenRouter returned an invalid key response")


def fetch_models(key: str) -> list[dict[str, Any]]:
    payload = api_json("models", key)
    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("OpenRouter returned an invalid model catalog")
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if not isinstance(model_id, str) or not model_id or model_id in seen:
            continue
        seen.add(model_id)
        models.append(item)
    if not models:
        raise RuntimeError("OpenRouter returned an empty model catalog")
    return models


def save_catalog(models: list[dict[str, Any]]) -> None:
    atomic_write_json(
        catalog_path(),
        {
            "version": 1,
            "source": f"{api_base()}/models",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "models": models,
        },
    )


def refresh_catalog(key: str | None = None) -> list[dict[str, Any]]:
    models = fetch_models(key or read_credential())
    save_catalog(models)
    return models


def load_catalog() -> list[dict[str, Any]]:
    path = catalog_path()
    try:
        document = read_json_object(path)
    except FileNotFoundError as exc:
        raise RuntimeError(f"model index not found at {path}; run index") from exc
    models = document.get("models")
    if not isinstance(models, list):
        raise RuntimeError(f"invalid model index at {path}; run index again")
    return [item for item in models if isinstance(item, dict) and isinstance(item.get("id"), str)]

