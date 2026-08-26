from __future__ import annotations

import pytest

from claude_openrouter.models import exact_models, picker_row, search_models


def ids(models: list[dict[str, object]]) -> list[str]:
    return [str(model["id"]) for model in models]


def test_plain_search_is_case_insensitive_substring_glob(sample_models) -> None:
    result = search_models(sample_models, ["CLAUDE"])
    assert ids(result) == [
        "anthropic/claude-opus-4.6",
        "anthropic/claude-sonnet-4.6",
    ]


def test_shell_style_search_matches_each_metadata_field(sample_models) -> None:
    assert ids(search_models(sample_models, ["anthropic/*sonnet*"])) == [
        "anthropic/claude-sonnet-4.6"
    ]
    assert ids(search_models(sample_models, ["*coding model"])) == [
        "qwen/qwen3-coder",
        "anthropic/claude-sonnet-4.6",
    ]


def test_multiple_queries_are_or_patterns(sample_models) -> None:
    assert set(ids(search_models(sample_models, ["gemini", "qwen*"]))) == {
        "google/gemini-3.1-pro-preview",
        "qwen/qwen3-coder",
    }


def test_regex_search_and_error(sample_models) -> None:
    assert ids(search_models(sample_models, [r"^google/.+preview$"], regex=True)) == [
        "google/gemini-3.1-pro-preview"
    ]
    with pytest.raises(ValueError, match="invalid regular expression"):
        search_models(sample_models, ["["], regex=True)


def test_exact_models_preserves_order_and_rejects_unknown(sample_models) -> None:
    selected = exact_models(
        sample_models,
        ["qwen/qwen3-coder", "anthropic/claude-opus-4.6", "qwen/qwen3-coder"],
    )
    assert ids(selected) == ["qwen/qwen3-coder", "anthropic/claude-opus-4.6"]
    with pytest.raises(ValueError, match="not found"):
        exact_models(sample_models, ["missing/model"])


def test_picker_row_has_human_metadata_and_management_marker(sample_models) -> None:
    row = picker_row(sample_models[0])
    assert row["model"] == "anthropic/claude-sonnet-4.6"
    assert row["label"] == "Claude Sonnet 4.6"
    assert "OpenRouter via claude-openrouter" in row["description"]
    assert "$3/M input" in row["description"]
