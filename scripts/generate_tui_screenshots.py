#!/usr/bin/env python3
"""Generate SVG screenshots of the TUI for the documentation site.

Drives each top-level TUI screen with Textual's headless test pilot (the same
mechanism the TUI test-suite uses) against a throwaway project populated with
sample transcript data, then exports a crisp, scalable SVG per screen.

Used two ways:

* By the MkDocs build (``docs/gen_pages.py`` via ``mkdocs-gen-files``), wrapped
  in error handling so a screenshot hiccup never breaks the prose docs.
* Standalone: ``python scripts/generate_tui_screenshots.py [OUTPUT_DIR]``
  (defaults to ``docs/assets/tui``).

SVG is chosen over PNG: it is small, sharp at any zoom, diff-friendly, and needs
no headless browser — Textual renders it natively.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from claude_code_log.tui import ProjectSelector, SessionBrowser

# A tiny but representative two-session transcript so the Session Browser table
# has something to show. Mirrors the shape used by the TUI test fixtures.
_SAMPLE_TRANSCRIPT: list[dict[str, object]] = [
    {
        "type": "user",
        "sessionId": "session-123",
        "timestamp": "2025-01-01T10:00:00Z",
        "uuid": "user-uuid-1",
        "message": {"role": "user", "content": "Help me refactor the parser"},
        "parentUuid": None,
        "isSidechain": False,
        "userType": "human",
        "cwd": "/home/dev/project-alpha",
        "version": "1.0.0",
        "isMeta": False,
    },
    {
        "type": "assistant",
        "sessionId": "session-123",
        "timestamp": "2025-01-01T10:01:00Z",
        "uuid": "assistant-uuid-1",
        "message": {
            "id": "msg-123",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "content": [{"type": "text", "text": "Sure — let's start with parser.py."}],
            "usage": {
                "input_tokens": 1200,
                "output_tokens": 350,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        },
        "parentUuid": "user-uuid-1",
        "isSidechain": False,
        "userType": "human",
        "cwd": "/home/dev/project-alpha",
        "version": "1.0.0",
        "requestId": "req-123",
    },
    {
        "type": "user",
        "sessionId": "session-456",
        "timestamp": "2025-01-02T14:30:00Z",
        "uuid": "user-uuid-2",
        "message": {"role": "user", "content": "Add a date-range filter to the CLI"},
        "parentUuid": None,
        "isSidechain": False,
        "userType": "human",
        "cwd": "/home/dev/project-alpha",
        "version": "1.0.0",
        "isMeta": False,
    },
]


@dataclass(frozen=True)
class Screenshot:
    """A generated screenshot and the caption to show under it."""

    filename: str
    title: str
    caption: str


def _write_sample_project(parent: Path) -> Path:
    """Create a project dir with a sample JSONL transcript; return its path."""
    project = parent / "-home-dev-project-alpha"
    project.mkdir(parents=True, exist_ok=True)
    transcript = project / "session-123.jsonl"
    transcript.write_text(
        "\n".join(json.dumps(entry) for entry in _SAMPLE_TRANSCRIPT) + "\n",
        encoding="utf-8",
    )
    return project


async def _capture(out_dir: Path) -> list[Screenshot]:
    out_dir.mkdir(parents=True, exist_ok=True)
    shots: list[Screenshot] = []

    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp)
        project = _write_sample_project(parent)

        # Session Browser — the main interface.
        browser = SessionBrowser(project)
        async with browser.run_test(size=(110, 32)) as pilot:
            # Sessions load asynchronously on mount; let the table populate.
            await pilot.pause()
            await pilot.pause(0.5)
            browser.save_screenshot(str(out_dir / "session-browser.svg"))
        shots.append(
            Screenshot(
                "session-browser.svg",
                "Session Browser",
                "Browse, export, and resume the sessions within a project.",
            )
        )

        # Project Selector — shown when multiple projects are found.
        selector = ProjectSelector(projects=[project], matching_projects=[project])
        async with selector.run_test(size=(110, 32)) as pilot:
            await pilot.pause()
            selector.save_screenshot(str(out_dir / "project-selector.svg"))
        shots.append(
            Screenshot(
                "project-selector.svg",
                "Project Selector",
                "Pick a project when several are discovered under ~/.claude/projects/.",
            )
        )

    return shots


def generate_screenshots(out_dir: Path) -> list[Screenshot]:
    """Generate the screenshots into ``out_dir``; return their metadata."""
    return asyncio.run(_capture(out_dir))


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/assets/tui")
    generated = generate_screenshots(target)
    for shot in generated:
        print(f"Wrote {target / shot.filename}")
