#!/usr/bin/env python3
"""Generate a Markdown keybindings reference from the TUI's Textual ``BINDINGS``.

The interactive TUI (``claude_code_log/tui.py``) declares its keyboard shortcuts
as static ``BINDINGS`` class variables on each Textual ``App`` / ``ModalScreen``
subclass. Rather than hand-maintaining a docs table that drifts from the source,
this module introspects those classes and renders a Markdown table per screen.

Used two ways:

* By the MkDocs build (``docs/gen_pages.py`` via ``mkdocs-gen-files``) so the
  published reference is always live.
* Standalone (``python scripts/generate_tui_docs.py``) to print the Markdown,
  handy for a quick local check.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass

from textual.app import App
from textual.binding import Binding
from textual.screen import Screen

from claude_code_log import tui


@dataclass(frozen=True)
class ScreenBindings:
    """A TUI screen and the keybindings it declares."""

    name: str
    summary: str
    bindings: list[Binding]


def _friendly_name(cls: type) -> str:
    """Turn a class name like ``ProjectSelector`` into ``Project Selector``."""
    import re

    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", cls.__name__)
    # Drop the noisy "Screen" suffix from modal class names.
    return spaced.removesuffix(" Screen").strip()


def _summary(cls: type) -> str:
    """First line of the class docstring, used as a short description."""
    doc = inspect.getdoc(cls) or ""
    return doc.strip().split("\n", 1)[0]


def collect_screen_bindings() -> list[ScreenBindings]:
    """Find every TUI class that declares its own non-empty ``BINDINGS``.

    Apps (top-level interfaces) are listed before modal screens (dialogs), and
    each group keeps source order so the docs read top-down like the code.
    """
    apps: list[ScreenBindings] = []
    screens: list[ScreenBindings] = []

    for _, cls in inspect.getmembers(tui, inspect.isclass):
        if cls.__module__ != tui.__name__:
            continue
        # Only consider classes that declare BINDINGS themselves, not inherited.
        bindings = cls.__dict__.get("BINDINGS")
        if not bindings:
            continue
        entry = ScreenBindings(
            name=_friendly_name(cls),
            summary=_summary(cls),
            bindings=[b for b in bindings if isinstance(b, Binding)],
        )
        if issubclass(cls, App):
            apps.append(entry)
        elif issubclass(cls, Screen):
            screens.append(entry)

    # Sort by the line number where each class is defined to preserve reading
    # order within each group.
    def _lineno(name: str) -> int:
        for obj_name, cls in inspect.getmembers(tui, inspect.isclass):
            if _friendly_name(cls) == name and cls.__module__ == tui.__name__:
                try:
                    return inspect.getsourcelines(cls)[1]
                except (OSError, TypeError):
                    return 0
        return 0

    apps.sort(key=lambda e: _lineno(e.name))
    screens.sort(key=lambda e: _lineno(e.name))
    return apps + screens


def _render_table(bindings: list[Binding]) -> str:
    rows = ["| Key | Action | Description |", "| --- | --- | --- |"]
    for binding in bindings:
        # `key` can be comma-separated (e.g. "tab,shift+tab"); show each nicely.
        keys = " / ".join(f"`{k.strip()}`" for k in binding.key.split(","))
        description = binding.description or ""
        if not binding.show:
            description = f"{description} _(hidden from footer)_".strip()
        action = f"`{binding.action}`"
        rows.append(f"| {keys} | {action} | {description} |")
    return "\n".join(rows)


def build_keybindings_markdown() -> str:
    """Render the full keybindings reference as Markdown."""
    parts: list[str] = [
        "# TUI Keybindings",
        "",
        (
            "These tables are generated directly from the Textual `BINDINGS` "
            "declared in `claude_code_log/tui.py`, so they always match the "
            "shipped TUI. Keys marked _(hidden from footer)_ work but are not "
            "shown in the on-screen footer."
        ),
        "",
    ]
    for screen in collect_screen_bindings():
        parts.append(f"## {screen.name}")
        parts.append("")
        if screen.summary:
            parts.append(screen.summary)
            parts.append("")
        parts.append(_render_table(screen.bindings))
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


if __name__ == "__main__":
    print(build_keybindings_markdown())
