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


class RecordingScreen:
    def __init__(self) -> None:
        self.values: list[str] = []

    def erase(self) -> None:
        pass

    def getmaxyx(self) -> tuple[int, int]:
        return (24, 100)

    def addnstr(self, _row: int, _column: int, value: str, _limit: int, _style: int = 0) -> None:
        self.values.append(value)

    def move(self, _row: int, _column: int) -> None:
        pass

    def refresh(self) -> None:
        pass


def run_picker(monkeypatch, sample_models, keys):
    states: list[tuple[str, int, bool]] = []
    screen = FakeScreen(keys)
    monkeypatch.setattr(picker, "_init_colors", lambda: None)
    monkeypatch.setattr(picker.curses, "raw", lambda: None)
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


def test_control_s_saves_while_search_has_focus(monkeypatch, sample_models) -> None:
    result, _states = run_picker(
        monkeypatch,
        sample_models,
        [*"sonnet", curses.KEY_DOWN, " ", "\x1b", "\x13"],
    )

    assert result == ["anthropic/claude-sonnet-4.6"]


def test_shift_s_saves_while_search_has_focus(monkeypatch, sample_models) -> None:
    result, _states = run_picker(
        monkeypatch,
        sample_models,
        [*"sonnet", curses.KEY_DOWN, " ", "\x1b", "S"],
    )

    assert result == ["anthropic/claude-sonnet-4.6"]


def test_lowercase_s_remains_available_for_search(monkeypatch, sample_models) -> None:
    result, states = run_picker(monkeypatch, sample_models, ["s", "\x03"])

    assert result is None
    assert states[-1] == ("s", 0, True)


def test_search_help_shows_save_shortcuts(monkeypatch, sample_models) -> None:
    screen = RecordingScreen()
    monkeypatch.setattr(picker, "_set_cursor", lambda _visible: None)
    monkeypatch.setattr(picker, "_COLORS_ENABLED", False)

    picker._draw(screen, sample_models, "sonnet", 0, [], True)

    assert any("Ctrl-S/Shift-S save" in value for value in screen.values)
