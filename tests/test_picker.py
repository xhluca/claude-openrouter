from __future__ import annotations

import curses
from typing import Any

from claude_openrouter import picker


class FakeScreen:
    def __init__(self, keys: list[Any]) -> None:
        self.keys = keys

    def keypad(self, _enabled: bool) -> None:
        pass

    def get_wch(self) -> Any:
        return self.keys.pop(0)


def run_picker(monkeypatch, sample_models, keys):
    states: list[tuple[str, int, bool]] = []
    screen = FakeScreen(keys)
    monkeypatch.setattr(picker.curses, "use_default_colors", lambda: None)
    monkeypatch.setattr(picker.curses, "wrapper", lambda run: run(screen))
    monkeypatch.setattr(
        picker,
        "_draw",
        lambda _screen, _models, query, cursor, _selected, search_mode: states.append(
            (query, cursor, search_mode)
        ),
    )
    result = picker._curses_picker(sample_models, [])
    return result, states


def test_down_from_search_focuses_first_result(monkeypatch, sample_models) -> None:
    result, states = run_picker(monkeypatch, sample_models, ["c", curses.KEY_DOWN, "q"])

    assert result is None
    assert states[-1] == ("c", 0, False)


def test_up_past_first_result_returns_to_search(monkeypatch, sample_models) -> None:
    result, states = run_picker(
        monkeypatch,
        sample_models,
        ["c", curses.KEY_DOWN, curses.KEY_UP, "\x03"],
    )

    assert result is None
    assert states[-1] == ("c", 0, True)


def test_escape_from_results_returns_to_search(monkeypatch, sample_models) -> None:
    result, states = run_picker(
        monkeypatch,
        sample_models,
        ["c", curses.KEY_DOWN, "\x1b", "\x03"],
    )

    assert result is None
    assert states[-1] == ("c", 0, True)
