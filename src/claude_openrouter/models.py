"""Model matching, ranking, and display helpers."""

from __future__ import annotations

import fnmatch
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

OPENROUTER_MODEL_PREFIX = "clor/openrouter/"


def input_modalities(model: dict[str, Any]) -> frozenset[str] | None:
    """Return normalized catalog input modalities, or ``None`` when unknown."""
    architecture = model.get("architecture")
    if not isinstance(architecture, dict):
        return None
    values = architecture.get("input_modalities")
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        return None
    return frozenset(value.casefold() for value in values)


def catalog_input_modalities(
    models: list[dict[str, Any]],
) -> dict[str, frozenset[str]]:
    """Index known input capabilities by exact OpenRouter model id."""
    result: dict[str, frozenset[str]] = {}
    for model in models:
        model_id = model.get("id")
        modalities = input_modalities(model)
        if isinstance(model_id, str) and modalities is not None:
            result[model_id] = modalities
    return result


def namespaced_model(model_id: str) -> str:
    return f"{OPENROUTER_MODEL_PREFIX}{model_id}"


def original_model(model_id: str) -> str | None:
    if not model_id.startswith(OPENROUTER_MODEL_PREFIX):
        return None
    original = model_id[len(OPENROUTER_MODEL_PREFIX) :]
    return original or None


def hybrid_openrouter_allowed(model_id: str) -> bool:
    normalized = model_id.casefold()
    return not normalized.startswith("anthropic/") and normalized != "openrouter/auto"


def searchable_text(model: dict[str, Any]) -> str:
    values = (model.get("id"), model.get("name"), model.get("description"))
    return "\n".join(value for value in values if isinstance(value, str))


def searchable_fields(model: dict[str, Any]) -> list[str]:
    values = (model.get("id"), model.get("name"), model.get("description"))
    return [value for value in values if isinstance(value, str)]


def _glob_pattern(query: str) -> str:
    return query if any(marker in query for marker in "*?[") else f"*{query}*"


def search_models(
    models: list[dict[str, Any]], queries: list[str], *, regex: bool = False
) -> list[dict[str, Any]]:
    if not queries:
        return list(models)
    if regex:
        try:
            patterns = [re.compile(query, re.IGNORECASE) for query in queries]
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc}") from exc

        def matches(model: dict[str, Any]) -> bool:
            fields = searchable_fields(model)
            return any(
                pattern.search(field) is not None
                for field in fields
                for pattern in patterns
            )

    else:
        patterns = [_glob_pattern(query).casefold() for query in queries]

        def matches(model: dict[str, Any]) -> bool:
            fields = [field.casefold() for field in searchable_fields(model)]
            return any(
                fnmatch.fnmatchcase(field, pattern)
                for field in fields
                for pattern in patterns
            )

    found = [model for model in models if matches(model)]
    return sorted(found, key=lambda model: _rank(model, queries))


def _rank(model: dict[str, Any], queries: list[str]) -> tuple[int, int, str]:
    model_id = str(model.get("id", "")).casefold()
    name = str(model.get("name", "")).casefold()
    plain = [query.casefold().strip("*?") for query in queries]
    score = 50
    for query in plain:
        if not query:
            continue
        if model_id == query:
            score = min(score, 0)
        elif name == query:
            score = min(score, 1)
        elif model_id.endswith(f"/{query}"):
            score = min(score, 2)
        elif query in model_id:
            score = min(score, 4 + model_id.index(query))
        elif query in name:
            score = min(score, 6 + name.index(query))
    return score, len(model_id), model_id


def top_matches(
    models: list[dict[str, Any]], query: str, *, limit: int = 15
) -> list[dict[str, Any]]:
    if not query.strip():
        return models[:limit]
    return search_models(models, [query])[:limit]


def exact_models(models: list[dict[str, Any]], ids: list[str]) -> list[dict[str, Any]]:
    by_id = {str(model["id"]): model for model in models}
    selected: list[dict[str, Any]] = []
    missing: list[str] = []
    seen: set[str] = set()
    for model_id in ids:
        if model_id in seen:
            continue
        seen.add(model_id)
        model = by_id.get(model_id)
        if model is None:
            missing.append(model_id)
        else:
            selected.append(model)
    if missing:
        rendered = ", ".join(missing)
        raise ValueError(f"model not found in the current OpenRouter index: {rendered}")
    if not selected:
        raise ValueError("select at least one model")
    return selected


def _price_per_million(value: Any) -> str | None:
    try:
        price = Decimal(str(value)) * 1_000_000
    except (InvalidOperation, TypeError, ValueError):
        return None
    if price == 0:
        return "free"
    return f"${price.normalize():f}/M"


def picker_description(model: dict[str, Any]) -> str:
    parts = [str(model.get("id", "")), "OpenRouter via claude-openrouter"]
    context = model.get("context_length")
    if isinstance(context, int) and context > 0:
        parts.append(f"{context // 1000}K context" if context >= 1000 else f"{context} context")
    pricing = model.get("pricing")
    if isinstance(pricing, dict):
        prompt = _price_per_million(pricing.get("prompt"))
        completion = _price_per_million(pricing.get("completion"))
        if prompt and completion:
            parts.append(f"{prompt} input · {completion} output")
    return " · ".join(parts)[:240]


def picker_row(model: dict[str, Any], *, hybrid: bool = False) -> dict[str, str]:
    model_id = str(model["id"])
    name = model.get("name")
    label = name if isinstance(name, str) and name else model_id
    return {
        "model": namespaced_model(model_id) if hybrid else model_id,
        "label": f"{label} · OpenRouter" if hybrid else label,
        "description": picker_description(model),
    }


def compact_row(model: dict[str, Any]) -> str:
    model_id = str(model.get("id", ""))
    name = model.get("name")
    context = model.get("context_length")
    context_text = f"{context:,}" if isinstance(context, int) else "-"
    label = name if isinstance(name, str) else ""
    return f"{model_id}\t{label}\t{context_text}"


def print_models(models: list[dict[str, Any]], *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(models, indent=2, ensure_ascii=False))
        return
    print("MODEL\tNAME\tCONTEXT")
    for model in models:
        print(compact_row(model))
