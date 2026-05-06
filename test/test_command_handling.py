#!/usr/bin/env python3
"""Test cases for command message handling and parsing."""

import json
import tempfile
from pathlib import Path
from claude_code_log.converter import load_transcript
from claude_code_log.html.renderer import generate_html


def test_slash_command_handling():
    """Test that user messages with slash commands are rendered with correct CSS class.

    Slash command messages (containing <command-name> tags) are user messages,
    not system messages. They should render with "user slash-command" CSS class.
    """
    command_message = {
        "type": "user",
        "timestamp": "2025-06-11T22:44:17.436Z",
        "parentUuid": None,
        "isSidechain": False,
        "userType": "human",
        "cwd": "/tmp",
        "sessionId": "test_session",
        "version": "1.0.0",
        "uuid": "test_cmd_001",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": '<command-message>init is analyzing your codebase…</command-message>\n<command-name>init</command-name>\n<command-args></command-args>\n<command-contents>{"type": "text", "text": "Please analyze this codebase..."}</command-contents>',
                }
            ],
        },
    }

    # Test that command messages are processed correctly end-to-end
    # (We can't test extraction directly on raw dicts because they need to be parsed first)

    # Test HTML generation
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(json.dumps(command_message) + "\n")
        f.flush()
        test_file_path = Path(f.name)

    try:
        messages = load_transcript(test_file_path)
        html = generate_html(messages, "Test Transcript")

        # Check if content is present (short content shown inline, long content collapsible)
        content = '{"type": "text", "text": "Please analyze this codebase..."}'
        lines = content.splitlines()
        if len(lines) > 12:
            assert "collapsible-code" in html, (
                "Should contain collapsible-code element for long content"
            )
        else:
            # For short content, should have pre tag with the escaped content
            assert "<pre>" in html, "Should contain pre tag for short content"
        # Bare ``<command-name>init</command-name>`` (legacy harness emission)
        # is normalised to ``/init`` by ``create_slash_command_message`` so
        # display is consistent with modern ``/init``-prefixed emissions.
        assert "<code>/init</code>" in html, (
            "Should show normalised command name (/init)"
        )
        # Check for user slash-command CSS class (not "system")
        # These are user messages with command tags, not system messages
        assert "class='message user slash-command" in html, (
            "Should have 'user slash-command' CSS class"
        )

    finally:
        test_file_path.unlink()


if __name__ == "__main__":
    test_slash_command_handling()
    print("\n✅ All command handling tests passed!")
