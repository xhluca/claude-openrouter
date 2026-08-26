"""Dependency-free interactive multi-model picker."""

from __future__ import annotations

import curses
import os
import sys
from contextlib import suppress
from typing import Any

from .models import top_matches

PAIR_TITLE = 1
PAIR_ACCENT = 2
PAIR_SUCCESS = 3
PAIR_QUERY = 4
PAIR_CURSOR = 5
_COLORS_ENABLED = False


def _init_colors() -> None:
    global _COLORS_ENABLED
    _COLORS_ENABLED = False
    if "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb":
        return
    try:
        curses.start_color()
        if not curses.has_colors():
            return
        background = -1
        try:
            curses.use_default_colors()
        except curses.error:
            background = curses.COLOR_BLACK
        curses.init_pair(PAIR_TITLE, curses.COLOR_MAGENTA, background)
        curses.init_pair(PAIR_ACCENT, curses.COLOR_CYAN, background)
        curses.init_pair(PAIR_SUCCESS, curses.COLOR_GREEN, background)
        curses.init_pair(PAIR_QUERY, curses.COLOR_YELLOW, background)
        curses.init_pair(PAIR_CURSOR, curses.COLOR_BLACK, curses.COLOR_CYAN)
    except curses.error:
        return
    _COLORS_ENABLED = True


def _style(pair: int, attributes: int = curses.A_NORMAL) -> int:
    return attributes | (curses.color_pair(pair) if _COLORS_ENABLED else 0)


def _add_segments(
    screen: Any,
    row: int,
    width: int,
    segments: list[tuple[str, int]],
) -> None:
    column = 0
    limit = max(1, width - 1)
    for value, style in segments:
        remaining = limit - column
        if remaining <= 0:
            break
        screen.addnstr(row, column, value, remaining, style)
        column += min(len(value), remaining)


def _set_cursor(visible: bool) -> None:
    with suppress(curses.error):
        curses.curs_set(1 if visible else 0)


def _ordered_toggle(selected: list[str], model_id: str) -> None:
    if model_id in selected:
        selected.remove(model_id)
    else:
        selected.append(model_id)


def _draw(
    screen: Any,
    models: list[dict[str, Any]],
    query: str,
    cursor: int,
    selected: list[str],
    search_mode: bool,
) -> None:
    screen.erase()
    height, width = screen.getmaxyx()
    title = "Claude OpenRouter — choose /model favorites"
    screen.addnstr(0, 0, title, max(1, width - 1), _style(PAIR_TITLE, curses.A_BOLD))
    prompt = f"Search: {query}"
    _add_segments(
        screen,
        2,
        width,
        [
            ("Search: ", _style(PAIR_ACCENT, curses.A_BOLD)),
            (query, _style(PAIR_QUERY, curses.A_BOLD)),
        ],
    )
    if search_mode:
        screen.addnstr(
            3,
            0,
            "Type to filter · ↓/Enter browse · Ctrl-S/Shift-S save · Ctrl-C cancel",
            width - 1,
            _style(PAIR_ACCENT, curses.A_DIM),
        )
    else:
        screen.addnstr(
            3,
            0,
            "↑/↓ move · Enter/Space select · Esc search · s save · q cancel",
            width - 1,
            _style(PAIR_ACCENT, curses.A_DIM),
        )
    _add_segments(
        screen,
        4,
        width,
        [
            ("Selected: ", _style(PAIR_ACCENT, curses.A_BOLD)),
            (str(len(selected)), _style(PAIR_SUCCESS, curses.A_BOLD)),
        ],
    )
    available_rows = max(1, height - 6)
    start = max(0, cursor - available_rows + 1)
    for row_index, model in enumerate(models[start : start + available_rows], start=5):
        absolute = start + row_index - 5
        model_id = str(model["id"])
        mark = "●" if model_id in selected else "○"
        name = model.get("name")
        suffix = f" — {name}" if isinstance(name, str) and name != model_id else ""
        if not search_mode and absolute == cursor:
            screen.addnstr(
                row_index,
                0,
                f"{mark} {model_id}{suffix}",
                width - 1,
                _style(PAIR_CURSOR, curses.A_BOLD),
            )
        else:
            mark_style = _style(
                PAIR_SUCCESS if model_id in selected else PAIR_ACCENT,
                curses.A_BOLD if model_id in selected else curses.A_DIM,
            )
            _add_segments(
                screen,
                row_index,
                width,
                [
                    (f"{mark} ", mark_style),
                    (model_id, _style(PAIR_ACCENT, curses.A_BOLD)),
                    (suffix, curses.A_DIM),
                ],
            )
    if search_mode:
        screen.move(2, min(width - 1, len(prompt)))
        _set_cursor(True)
    else:
        _set_cursor(False)
    screen.refresh()


def _curses_picker(models: list[dict[str, Any]], initial: list[str]) -> list[str] | None:
    def run(screen: Any) -> list[str] | None:
        _init_colors()
        curses.raw()
        screen.keypad(True)
        selected = [model_id for model_id in initial if any(m["id"] == model_id for m in models)]
        query = ""
        results = top_matches(models, query)
        cursor = 0
        search_mode = True
        while True:
            _draw(screen, results, query, cursor, selected, search_mode)
            key = screen.get_wch()
            if search_mode:
                if key in ("\x13", "S"):
                    if selected:
                        return selected
                    curses.beep()
                elif key in ("\n", "\r", curses.KEY_ENTER, curses.KEY_DOWN):
                    if results:
                        search_mode = False
                        cursor = 0
                    else:
                        curses.beep()
                elif key in ("\b", "\x7f", curses.KEY_BACKSPACE):
                    query = query[:-1]
                    results = top_matches(models, query)
                    cursor = 0
                elif key == "\x03":
                    return None
                elif key == "\x1b":
                    continue
                elif isinstance(key, str) and key.isprintable():
                    query += key
                    results = top_matches(models, query)
                    cursor = 0
                continue

            if key in (curses.KEY_UP, "k"):
                if cursor == 0:
                    search_mode = True
                else:
                    cursor -= 1
            elif key in (curses.KEY_DOWN, "j"):
                cursor = min(max(0, len(results) - 1), cursor + 1)
            elif key in ("\n", "\r", " ", curses.KEY_ENTER) and results:
                _ordered_toggle(selected, str(results[cursor]["id"]))
            elif key == "/":
                query = ""
                results = top_matches(models, query)
                cursor = 0
                search_mode = True
            elif key == "\x1b":
                search_mode = True
            elif key in ("s", "S"):
                if selected:
                    return selected
                curses.beep()
            elif key in ("q", "Q", "\x03"):
                return None

    return curses.wrapper(run)


def _line_picker(models: list[dict[str, Any]], initial: list[str]) -> list[str] | None:
    selected = [model_id for model_id in initial if any(m["id"] == model_id for m in models)]
    while True:
        query = input("Search models (q cancels): ").strip()
        if query.lower() == "q":
            return None
        results = top_matches(models, query, limit=12)
        if not results:
            print("No matches.")
            continue
        for index, model in enumerate(results, 1):
            model_id = str(model["id"])
            mark = "x" if model_id in selected else " "
            print(f"  {index:>2}) [{mark}] {model_id}")
        while True:
            action = input("Toggle number(s), / search, s save, q cancel: ").strip().lower()
            if action == "/":
                break
            if action == "q":
                return None
            if action == "s":
                if selected:
                    return selected
                print("Select at least one model.", file=sys.stderr)
                continue
            try:
                indices = [int(value) for value in action.replace(",", " ").split()]
                if not indices:
                    raise ValueError
                for index in indices:
                    _ordered_toggle(selected, str(results[index - 1]["id"]))
            except (ValueError, IndexError):
                print("Enter result numbers, /, s, or q.", file=sys.stderr)


def choose_models(models: list[dict[str, Any]], initial: list[str]) -> list[str] | None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError("interactive selection needs a terminal; use --model or --models")
    try:
        return _curses_picker(models, initial)
    except curses.error:
        return _line_picker(models, initial)
