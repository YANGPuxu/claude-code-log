"""Playwright browser tests for teammates-feature HTML rendering.

Exercises the rendered-in-browser output so regressions that only
surface once CSS applies (computed colors, table layout, filter
visibility) get caught end to end.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import List

import pytest
from playwright.sync_api import Page

from claude_code_log.converter import load_directory_transcripts
from claude_code_log.html.renderer import generate_html


pytestmark = pytest.mark.browser


FIXTURE_DIR = Path(__file__).parent / "test_data" / "teammates"


class TestTeammatesBrowser:
    """Browser-level assertions on the teammates fixture rendering."""

    def setup_method(self) -> None:
        self._tmp_files: List[Path] = []

    def teardown_method(self) -> None:
        for p in self._tmp_files:
            try:
                p.unlink()
            except FileNotFoundError:
                pass

    def _render(self) -> Path:
        # Use load_directory_transcripts so _integrate_agent_entries
        # runs and assigns synthetic sessionIds — the path that creates
        # the subagent session boundaries the rendering depends on.
        messages, session_tree = load_directory_transcripts(
            FIXTURE_DIR, cache_manager=None, silent=True
        )
        html = generate_html(messages, "Teammates Fixture", session_tree=session_tree)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as f:
            f.write(html)
            tmp = Path(f.name)
        self._tmp_files.append(tmp)
        return tmp

    def test_teammate_messages_render_with_distinct_colored_borders(
        self, page: Page
    ) -> None:
        """Each <teammate-message> block produces a card with a colored left border.

        The fixture has alice (blue) and bob (green) sending messages in a
        mixed-teammates entry, plus a system termination notice. After
        rendering we expect at least three .teammate-message cards with
        *distinct* computed ``border-left-color`` values across alice and bob.
        """
        page.goto(f"file://{self._render()}")

        # Transcript content lives inside collapsed <details> blocks by
        # default, so we check DOM attachment + computed styles rather
        # than visibility. CSS is still applied to folded content.
        teammate_messages = page.locator(".teammate-message")
        count = teammate_messages.count()
        assert count >= 3, f"expected at least 3 teammate-message cards, got {count}"

        # Collect computed left-border colors for each card. Distinct
        # values across alice and bob prove the --cc-color variable
        # routed correctly from the inline style to the cascade.
        border_colors: set[str] = set()
        for i in range(count):
            card = teammate_messages.nth(i)
            color = card.evaluate("el => getComputedStyle(el).borderLeftColor")
            border_colors.add(color)
        # Must have at least two distinct colors — alice and bob pick up
        # different --cc-<color> tokens. (System takes a neutral gray on
        # top of those, so the set size is typically 3.)
        assert len(border_colors) >= 2, (
            f"teammate-message cards share one border color, expected distinct: "
            f"{border_colors}"
        )

    def test_task_list_renders_as_html_table(self, page: Page) -> None:
        """TaskList tool output renders as a <table class="task-list"> with rows."""
        page.goto(f"file://{self._render()}")

        table = page.locator("table.task-list").first
        table_count = page.locator("table.task-list").count()
        assert table_count >= 1, "task-list table not rendered"

        rows = table.locator("tbody tr")
        row_count = rows.count()
        assert row_count >= 1, f"expected task-list rows, got {row_count}"

        # Header labels present in the DOM (content may be folded)
        assert table.locator("thead th", has_text="Status").count() == 1
        assert table.locator("thead th", has_text="Owner").count() == 1

    def test_subagent_content_inlined_under_anchor(self, page: Page) -> None:
        """Subagent content is inlined under its spawning Task/Agent
        tool_result, not wrapped in a separate `<details>` block.

        The post-merge refactor (commits ``27e43fb`` / ``fdd28ec``)
        dropped the `<details class="subagent-session-block">` wrap and
        the standalone subagent session header. Each subagent's
        sidechain assistant rows must now render directly in the
        document, threaded under their respective trunk anchors via
        ``_relocate_subagent_blocks``.
        """
        page.goto(f"file://{self._render()}")

        # The legacy collapse markup must be gone.
        assert page.locator("details.subagent-session-block").count() == 0
        assert page.locator(".session-teammate-badge").count() == 0

        # The fixture has alice + bob subagents; their sidechain
        # assistant rows should be present and visible without any
        # expand action.
        sidechain_assistants = page.locator(".message.assistant.sidechain")
        assert sidechain_assistants.count() >= 2, (
            "expected ≥2 subagent assistant rows inlined; got "
            f"{sidechain_assistants.count()}"
        )

    def test_teammate_badge_color_matches_teammate_id(self, page: Page) -> None:
        """The alice badge carries the blue token (via inline --cc-color).

        Regression for color propagation: when a TaskUpdate/SendMessage
        row names alice, her badge must inherit her --cc-blue color from
        the RenderingContext color map — not fall back to gray.
        """
        page.goto(f"file://{self._render()}")

        alice_badges = page.locator(".teammate-badge", has_text="alice")
        count = alice_badges.count()
        assert count >= 1, "no alice badges found"

        # Every alice badge must be colored blue-ish (the --cc-blue token
        # resolves to rgb(47, 128, 237)). Gray (rgb(107,114,128)) and
        # green (rgb(39,174,96)) would both fail the b > r and b > g
        # predicate below.
        for i in range(count):
            bg = alice_badges.nth(i).evaluate(
                "el => getComputedStyle(el).backgroundColor"
            )
            # Parse rgb(R, G, B)
            nums = [
                int(x.strip())
                for x in bg.removeprefix("rgb(").rstrip(")").split(",")[:3]
            ]
            r, g, b = nums
            assert b > r and b > g, (
                f"alice badge #{i} not blue-dominant: rgb({r}, {g}, {b})"
            )
