"""Dependency-free interactive multi-model picker."""

from __future__ import annotations

import curses
import sys
from typing import Any

from .models import top_matches


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
    screen.addnstr(0, 0, title, max(1, width - 1), curses.A_BOLD)
    prompt = f"Search: {query}"
    screen.addnstr(2, 0, prompt, max(1, width - 1))
    if search_mode:
        screen.addnstr(3, 0, "Type a term, then press Enter to browse matches.", width - 1)
    else:
        screen.addnstr(
            3,
            0,
            "↑/↓ move · Enter/Space select · / search · s save · q cancel",
            width - 1,
        )
    screen.addnstr(4, 0, f"Selected: {len(selected)}", width - 1, curses.A_BOLD)
    available_rows = max(1, height - 6)
    start = max(0, cursor - available_rows + 1)
    for row_index, model in enumerate(models[start : start + available_rows], start=5):
        absolute = start + row_index - 5
        model_id = str(model["id"])
        mark = "●" if model_id in selected else "○"
        name = model.get("name")
        suffix = f" — {name}" if isinstance(name, str) and name != model_id else ""
        style = curses.A_REVERSE if not search_mode and absolute == cursor else curses.A_NORMAL
        screen.addnstr(row_index, 0, f"{mark} {model_id}{suffix}", width - 1, style)
    if search_mode:
        screen.move(2, min(width - 1, len(prompt)))
        curses.curs_set(1)
    else:
        curses.curs_set(0)
    screen.refresh()


def _curses_picker(models: list[dict[str, Any]], initial: list[str]) -> list[str] | None:
    def run(screen: Any) -> list[str] | None:
        curses.use_default_colors()
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
                if key in ("\n", "\r", curses.KEY_ENTER):
                    search_mode = False
                    cursor = 0
                elif key in ("\b", "\x7f", curses.KEY_BACKSPACE):
                    query = query[:-1]
                    results = top_matches(models, query)
                    cursor = 0
                elif key == "\x1b":
                    return None
                elif isinstance(key, str) and key.isprintable():
                    query += key
                    results = top_matches(models, query)
                    cursor = 0
                continue

            if key in (curses.KEY_UP, "k"):
                cursor = max(0, cursor - 1)
            elif key in (curses.KEY_DOWN, "j"):
                cursor = min(max(0, len(results) - 1), cursor + 1)
            elif key in ("\n", "\r", " ", curses.KEY_ENTER) and results:
                _ordered_toggle(selected, str(results[cursor]["id"]))
            elif key == "/":
                query = ""
                results = top_matches(models, query)
                cursor = 0
                search_mode = True
            elif key in ("s", "S"):
                if selected:
                    return selected
                curses.beep()
            elif key in ("q", "Q", "\x1b"):
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

