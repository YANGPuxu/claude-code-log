"""Snapshot tests for Markdown output regression detection.

These tests use syrupy to capture and compare Markdown output, detecting
unintended changes to the rendered Markdown structure.
"""

import shutil
from pathlib import Path

import pytest

from claude_code_log.converter import convert_jsonl_to, load_transcript
from claude_code_log.markdown.renderer import MarkdownRenderer

# Run snapshot tests serially to ensure deterministic file ordering
pytestmark = pytest.mark.snapshot


class TestTranscriptMarkdownSnapshots:
    """Snapshot tests for transcript Markdown output."""

    def test_representative_messages_markdown(self, markdown_snapshot, test_data_dir):
        """Snapshot test for representative messages - core message types."""
        test_file = test_data_dir / "representative_messages.jsonl"
        messages = load_transcript(test_file)
        renderer = MarkdownRenderer()
        md = renderer.generate(messages, "Test Transcript")
        assert md == markdown_snapshot

    def test_edge_cases_markdown(self, markdown_snapshot, test_data_dir):
        """Snapshot test for edge cases - errors, special chars, long text."""
        test_file = test_data_dir / "edge_cases.jsonl"
        messages = load_transcript(test_file)
        renderer = MarkdownRenderer()
        md = renderer.generate(messages, "Edge Cases")
        assert md == markdown_snapshot

    def test_multi_session_markdown(self, markdown_snapshot, test_data_dir, tmp_path):
        """Snapshot test for multi-session combined output."""
        shutil.copy(
            test_data_dir / "representative_messages.jsonl",
            tmp_path / "session_a.jsonl",
        )
        shutil.copy(test_data_dir / "session_b.jsonl", tmp_path / "session_b.jsonl")

        md_file = convert_jsonl_to("md", tmp_path, use_cache=False)
        md = md_file.read_text(encoding="utf-8")
        assert md == markdown_snapshot


class TestSessionMarkdownSnapshots:
    """Snapshot tests for individual session Markdown output."""

    def test_individual_session_markdown(self, markdown_snapshot, test_data_dir):
        """Snapshot test for individual session file."""
        test_file = test_data_dir / "representative_messages.jsonl"
        messages = load_transcript(test_file)
        renderer = MarkdownRenderer()
        md = renderer.generate_session(messages, "test_session", "Test Session")
        assert md == markdown_snapshot


class TestTeammatesMarkdownSnapshots:
    """Snapshot tests for the teammates feature Markdown rendering."""

    def test_teammates_fixture_markdown(self, markdown_snapshot, test_data_dir):
        """Snapshot the teammates fixture rendered as Markdown.

        Covers the colored-circle emoji markers, the pipe-table
        TaskList, the six teammate tool shapes, and the blockquoted
        TeammateMessage bodies.
        """
        teammates_dir = test_data_dir / "teammates"
        main_jsonl = teammates_dir / "ef000000-0000-4000-8000-000000000001.jsonl"
        messages = load_transcript(main_jsonl)
        renderer = MarkdownRenderer()
        md = renderer.generate(messages, "Teammates Fixture")
        assert md == markdown_snapshot


class TestAsyncAgentsMarkdownSnapshots:
    """Snapshot tests for the async-agents feature Markdown rendering."""

    def test_async_agents_fixture_markdown(self, markdown_snapshot, test_data_dir):
        """Snapshot the async-agents fixture rendered as Markdown.

        Covers the ``*[async]*`` italic hint on the spawning Task title,
        the TaskOutput poll card, the ``Result (from async notification)``
        collapsible fold under the spawn, and the dedup-collapsed
        ``<task-notification>`` card.
        """
        async_dir = test_data_dir / "async_agents"
        main_jsonl = async_dir / "eb000000-0000-4000-8000-000000000001.jsonl"
        messages = load_transcript(main_jsonl)
        renderer = MarkdownRenderer()
        md = renderer.generate(messages, "Async Agents Fixture")
        assert md == markdown_snapshot


class TestIndexMarkdownSnapshots:
    """Snapshot tests for project index Markdown output."""

    def test_project_index_markdown(self, markdown_snapshot):
        """Snapshot test for project index template."""
        project_summaries = [
            {
                "name": "-Users-test-project-alpha",
                "path": Path("/tmp/project-alpha"),
                "html_file": "-Users-test-project-alpha/combined_transcripts.html",
                "jsonl_count": 5,
                "message_count": 42,
                "last_modified": 1700000000.0,
                "total_input_tokens": 1000,
                "total_output_tokens": 2000,
                "total_cache_creation_tokens": 500,
                "total_cache_read_tokens": 1500,
                "latest_timestamp": "2025-01-15T10:00:00Z",
                "earliest_timestamp": "2025-01-01T09:00:00Z",
                "working_directories": ["/Users/test/projects/alpha"],
                "sessions": [
                    {
                        "id": "session-abc12345",
                        "summary": "Test session summary",
                        "timestamp_range": "2025-01-15 10:00:00",
                        "message_count": 10,
                        "first_user_message": "Hello, this is a test",
                    }
                ],
            },
            {
                "name": "-Users-test-project-beta",
                "path": Path("/tmp/project-beta"),
                "html_file": "-Users-test-project-beta/combined_transcripts.html",
                "jsonl_count": 3,
                "message_count": 25,
                "last_modified": 1700000100.0,
            },
        ]

        renderer = MarkdownRenderer()
        md = renderer.generate_projects_index(project_summaries)
        assert md == markdown_snapshot
