#!/usr/bin/env python3
"""Utility functions for message filtering and processing."""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import (
    ContentItem,
    DetailLevel,
    TextContent,
    TranscriptEntry,
    UserTranscriptEntry,
)
from .factories import (
    IDE_DIAGNOSTICS_PATTERN,
    IDE_OPENED_FILE_PATTERN,
    IDE_SELECTION_PATTERN,
    is_command_message,
    is_local_command_output,
    is_system_message,
)


# Per-level output file naming
#
# Variants of the same project render to distinct filenames so each
# variant has its own on-disk artifact and its own cache row. Examples:
#
#   --detail full                          → combined_transcripts.html
#   --detail low                           → combined_transcripts.low.html
#   --detail low --compact (md only)       → combined_transcripts.low.compact.md
#   --compact (md only)                    → combined_transcripts.compact.md
#
# Pagination composes after the variant suffix:
#   combined_transcripts.low_2.html        (detail=low, page 2)
#
# `_compact` only participates in the suffix for Markdown output — HTML
# rendering ignores the flag, so `--compact --format html` is a silent
# no-op on the filename (matching the CLI description that compact is
# Markdown-only).

VARIANT_ENTRY_RE = re.compile(r"^combined_transcripts((?:\.[a-z]+)*)\.html$")


def variant_suffix(
    detail: DetailLevel | str = DetailLevel.FULL,
    compact: bool = False,
    format: str = "html",
) -> str:
    """Compute the filename infix for a given render variant.

    Returns the empty string for the default variant
    (full detail, no compact). Otherwise returns a dot-prefixed
    suffix that is inserted after the basename and before the page
    number / extension.
    """
    # `DetailLevel` inherits from `str`, so `isinstance(detail, str)` is
    # always True — narrow only on `DetailLevel` to coerce plain strings.
    if not isinstance(detail, DetailLevel):
        detail = DetailLevel(detail)
    parts: list[str] = []
    if detail != DetailLevel.FULL:
        parts.append(detail.value)
    # `--compact` is Markdown-only (merges consecutive same-category
    # headings in the Markdown renderer). For HTML it's a no-op.
    if compact and format in ("md", "markdown"):
        parts.append("compact")
    return "".join(f".{p}" for p in parts)


def format_timestamp(timestamp_str: str | None) -> str:
    """Format ISO timestamp for display, converting to UTC."""
    if timestamp_str is None:
        return ""
    try:
        dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        # Convert to UTC if timezone-aware
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return timestamp_str


def format_timestamp_range(first_timestamp: str, last_timestamp: str) -> str:
    """Format timestamp range for display.

    Args:
        first_timestamp: ISO timestamp for range start
        last_timestamp: ISO timestamp for range end

    Returns:
        Formatted string like "2025-01-01 10:00:00 - 2025-01-01 11:00:00"
        or single timestamp if both are equal, or empty string if neither provided.
    """
    if first_timestamp and last_timestamp:
        if first_timestamp == last_timestamp:
            return format_timestamp(first_timestamp)
        else:
            return f"{format_timestamp(first_timestamp)} - {format_timestamp(last_timestamp)}"
    elif first_timestamp:
        return format_timestamp(first_timestamp)
    else:
        return ""


def _is_temp_path(path_str: str) -> bool:
    """Check if a path is a temporary/test path that should be filtered out."""
    temp_patterns = [
        "/private/var/folders/",  # macOS temp
        "/tmp/",  # Unix temp
        "/var/folders/",  # macOS temp (alternate)
    ]
    return any(pattern in path_str for pattern in temp_patterns)


def get_project_display_name(
    project_dir_name: str, working_directories: Optional[list[str]] = None
) -> str:
    """Get the display name for a project based on working directories.

    Args:
        project_dir_name: The Claude project directory name (e.g., "-Users-dain-workspace-claude-code-log")
        working_directories: List of working directories from cache data

    Returns:
        The project display name (e.g., "claude-code-log")
    """
    if working_directories:
        # Filter out temporary paths (pytest, macOS temp dirs, etc.)
        real_dirs = [wd for wd in working_directories if not _is_temp_path(wd)]

        # If all directories were filtered out, fall back to project_dir_name conversion
        if not real_dirs:
            display_name = project_dir_name
            if display_name.startswith("-"):
                display_name = display_name[1:].replace("-", "/")
            return display_name

        # Convert to Path objects with their original indices for tracking recency
        paths_with_indices = [(Path(wd), i) for i, wd in enumerate(real_dirs)]

        # Sort by: 1) path depth (fewer parts = less nested), 2) recency (lower index = more recent)
        # This gives us the least nested path, with ties broken by recency
        best_path, _ = min(paths_with_indices, key=lambda p: (len(p[0].parts), p[1]))
        return best_path.name
    else:
        # Fall back to converting project directory name
        display_name = project_dir_name
        if display_name.startswith("-"):
            display_name = display_name[1:].replace("-", "/")
        return display_name


def should_skip_message(text_content: str) -> bool:
    """
    Determine if a message should be skipped in transcript rendering.

    This is the centralized logic for filtering out unwanted messages.
    """
    is_system = is_system_message(text_content)
    is_command = is_command_message(text_content)
    is_output = is_local_command_output(text_content)

    # Skip system messages that are not command messages AND not local command output
    return is_system and not is_command and not is_output


def extract_init_command_description(text_content: str) -> str:
    """
    Extract a meaningful description from init command content.

    Returns a user-friendly description for init commands instead of raw XML.
    """
    if "<command-name>init" in text_content and "<command-contents>" in text_content:
        return "Claude Initializes Codebase Documentation Guide (/init command)"
    return text_content


def should_use_as_session_starter(text_content: str) -> bool:
    """
    Determine if a user message should be used as a session starter preview.

    This filters out system messages, warmup messages, and most command messages,
    except for 'init' commands which are typically the start of a new session.
    """
    # Skip warmup messages
    if text_content.strip() == "Warmup":
        return False

    # Skip system messages
    if is_system_message(text_content):
        return False

    # Skip command messages except for 'init' commands
    if "<command-name>" in text_content:
        return "<command-name>init" in text_content

    return True


# Constants
FIRST_USER_MESSAGE_PREVIEW_LENGTH = 1000


def create_session_preview(text_content: str) -> str:
    """Create a truncated preview of first user message for session display.

    Args:
        text_content: The raw text content from the first user message

    Returns:
        A preview string, truncated to FIRST_USER_MESSAGE_PREVIEW_LENGTH with
        ellipsis if needed, with init commands converted to friendly descriptions,
        and IDE tags replaced with compact emoji indicators.
    """
    # Apply init command transformation first
    preview_content = extract_init_command_description(text_content)

    # Apply compact IDE tag indicators BEFORE truncation
    preview_content = _compact_ide_tags_for_preview(preview_content)

    # Then truncate if needed
    if len(preview_content) > FIRST_USER_MESSAGE_PREVIEW_LENGTH:
        return preview_content[:FIRST_USER_MESSAGE_PREVIEW_LENGTH] + "..."
    return preview_content


def extract_text_content_length(content: list[ContentItem]) -> int:
    """Get the length of text content for quick checks without full extraction."""
    total_length = 0
    for item in content:
        # Only count TextContent items, skip tool/thinking/image items
        if isinstance(item, TextContent):
            total_length += len(item.text.strip())
    return total_length


# IDE tag patterns imported from factories for compact preview rendering


def _compact_ide_tags_for_preview(text_content: str) -> str:
    """Replace verbose IDE/system tags with compact emoji indicators for previews.

    Only processes tags at the START of the content (where VS Code places them).
    Tags appearing later in the text (e.g., inside quoted JSONL) are left unchanged.

    Transforms:
    - <ide_opened_file>...path/to/file...</ide_opened_file> -> 📎 /path/to/file
    - <ide_selection>...path/to/file...</ide_selection> -> ✂️ /path/to/file
    - <ide_diagnostics>...</ide_diagnostics> -> 🩺 diagnostics
    - <bash-input>command</bash-input> -> 💻 command

    Args:
        text_content: Raw text content that may contain IDE/system tags

    Returns:
        Text with leading tags replaced by compact indicators
    """

    def _extract_file_path(content: str) -> str | None:
        """Extract file path from IDE tag content."""
        # Try to find an absolute path (starts with /)
        # Stop at: whitespace, colon followed by newline, or "in the IDE"
        path_match = re.search(
            r"(/[^\s:]+(?:\.[^\s:]+)?)(?::\s|\s+in\s+the\s+IDE|\s*$|\s)", content
        )
        if path_match:
            return path_match.group(1).rstrip(".:")

        # Fallback: look for "file" or "from" followed by a path
        path_match = re.search(r"(?:file|from)\s+(/[^\s:]+)", content)
        if path_match:
            return path_match.group(1).rstrip(".:")

        return None

    # Process only LEADING IDE tags - stop when we hit non-IDE content
    # This prevents replacing tags inside quoted strings/JSONL content
    # Uses shared patterns from parser.py for consistency
    compact_parts: list[str] = []
    remaining = text_content

    # Compiled pattern for bash-input (not in parser.py as it's preview-specific)
    bash_input_pattern = re.compile(r"<bash-input>(.*?)</bash-input>", re.DOTALL)

    while remaining:
        # Strip leading whitespace for matching
        stripped = remaining.lstrip()

        # Try to match each IDE tag type at the start of stripped text
        # Check for <ide_opened_file> at start (using shared pattern)
        match = IDE_OPENED_FILE_PATTERN.match(stripped)
        if match:
            content = match.group(1).strip()
            filepath = _extract_file_path(content)
            compact_parts.append(f"📎 {filepath}" if filepath else "📎 file")
            remaining = stripped[match.end() :]
            continue

        # Check for <ide_selection> at start (using shared pattern)
        match = IDE_SELECTION_PATTERN.match(stripped)
        if match:
            content = match.group(1).strip()
            filepath = _extract_file_path(content)
            compact_parts.append(f"✂️ {filepath}" if filepath else "✂️ selection")
            remaining = stripped[match.end() :]
            continue

        # Check for <post-tool-use-hook><ide_diagnostics>... (using shared pattern)
        match = IDE_DIAGNOSTICS_PATTERN.match(stripped)
        if match:
            compact_parts.append("🩺 diagnostics")
            remaining = stripped[match.end() :]
            continue

        # Check for <bash-input>command</bash-input> at start
        match = bash_input_pattern.match(stripped)
        if match:
            command = match.group(1).strip()
            # Truncate very long commands
            if len(command) > 50:
                command = command[:47] + "..."
            compact_parts.append(f"💻 {command}")
            remaining = stripped[match.end() :]
            continue

        # No more tags at start - stop processing
        break

    # Combine compact indicators with remaining content
    if compact_parts:
        # Add newline between indicators and content if there's remaining text
        prefix = "\n".join(compact_parts)
        if remaining.strip():
            return f"{prefix}\n{remaining.lstrip()}"
        return prefix

    return text_content


def is_agent_session(session_id: str) -> bool:
    """Check if a session ID is a synthetic agent session.

    Agent sessions use the format ``{sessionId}#agent-{agentId}``,
    assigned by ``_integrate_agent_entries()`` during DAG construction.
    """
    return "#agent-" in session_id


def get_parent_session_id(session_id: str) -> str:
    """Return the parent session ID for an agent session, or the ID itself.

    For ``{sessionId}#agent-{agentId}`` returns ``{sessionId}``.
    For non-agent sessions returns the input unchanged.
    """
    return session_id.split("#agent-")[0] if "#agent-" in session_id else session_id


def get_warmup_session_ids(messages: list[TranscriptEntry]) -> set[str]:
    """Get set of session IDs that are warmup-only sessions.

    Pre-computes warmup status for all sessions for efficiency (O(n) once,
    then O(1) lookup per session).

    Args:
        messages: List of all transcript entries

    Returns:
        Set of session IDs that contain only warmup messages
    """
    from .parser import extract_text_content

    # Group user message text by session
    session_user_messages: dict[str, list[str]] = {}

    for message in messages:
        if isinstance(message, UserTranscriptEntry) and hasattr(message, "message"):
            session_id = getattr(message, "sessionId", "")
            if session_id:
                text_content = extract_text_content(message.message.content).strip()
                if session_id not in session_user_messages:
                    session_user_messages[session_id] = []
                session_user_messages[session_id].append(text_content)

    # Find sessions where ALL user messages are "Warmup"
    warmup_sessions: set[str] = set()
    for session_id, user_msgs in session_user_messages.items():
        if user_msgs and all(msg == "Warmup" for msg in user_msgs):
            warmup_sessions.add(session_id)

    return warmup_sessions


def strip_error_tags(text: str) -> str:
    """Strip <tool_use_error>...</tool_use_error> tags, keeping content.

    Claude Code uses these XML-style tags to wrap error messages in tool results.
    This function strips the tags while preserving the error message content.

    Args:
        text: Text that may contain tool_use_error tags

    Returns:
        Text with error tags removed but content preserved
    """
    return re.sub(
        r"<tool_use_error>(.*?)</tool_use_error>",
        r"\1",
        text,
        flags=re.DOTALL,
    )


def generate_unified_diff(old_string: str, new_string: str) -> str:
    """Generate a unified diff between old and new strings.

    Args:
        old_string: The original content
        new_string: The modified content

    Returns:
        Unified diff as a string (without header lines)
    """
    import difflib

    old_lines = old_string.splitlines(keepends=True)
    new_lines = new_string.splitlines(keepends=True)

    # Ensure last lines end with newline for proper diff output
    if old_lines and not old_lines[-1].endswith("\n"):
        old_lines[-1] += "\n"
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"

    diff_lines = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=3))

    # Skip the header lines (--- and +++) if present
    if len(diff_lines) >= 2 and diff_lines[0].startswith("---"):
        diff_lines = diff_lines[2:]

    return "".join(diff_lines).rstrip("\n")
