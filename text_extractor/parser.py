"""Parser for Claude Code JSONL files."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ContentItem:
    """A content item within a message."""

    type: str
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None
    tool_use_id: str | None = None
    output: str | None = None
    error: str | None = None


@dataclass
class Message:
    """A parsed message from JSONL."""

    type: str
    uuid: str
    timestamp: str
    content: list[ContentItem] = field(default_factory=list)
    raw_content: str | list[dict] | None = None
    subtype: str | None = None
    is_meta: bool = False
    is_sidechain: bool = False
    agent_id: str | None = None
    attribution_agent: str | None = None
    last_prompt: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_line(cls, line: str) -> Message | None:
        """Parse a message from a JSONL line."""
        try:
            data = json.loads(line.strip())
        except json.JSONDecodeError:
            return None

        msg = cls(
            type=data.get("type", ""),
            uuid=data.get("uuid", ""),
            timestamp=data.get("timestamp", ""),
            raw=data,
        )

        # Parse metadata fields
        msg.is_sidechain = data.get("isSidechain", False)
        msg.is_meta = data.get("isMeta", False)
        msg.agent_id = data.get("agentId")
        msg.attribution_agent = data.get("attributionAgent")
        msg.subtype = data.get("subtype")
        msg.last_prompt = data.get("lastPrompt")

        # Parse content
        msg.raw_content = data.get("content")
        if "message" in data and isinstance(data["message"], dict):
            message_data = data["message"]
            content_list = message_data.get("content", [])
            msg.content = parse_content_list(content_list)
        elif isinstance(msg.raw_content, str):
            msg.content = [ContentItem(type="text", text=msg.raw_content)]

        return msg


def parse_content_list(content: Any) -> list[ContentItem]:
    """Parse a content list into ContentItem objects."""
    if not isinstance(content, list):
        return []

    items = []
    for item in content:
        if not isinstance(item, dict):
            continue

        content_type = item.get("type", "")
        content_item = ContentItem(type=content_type)

        if content_type == "text":
            content_item.text = item.get("text", "")
        elif content_type == "thinking":
            content_item.text = item.get("thinking", "")
        elif content_type == "tool_use":
            content_item.id = item.get("id")
            content_item.name = item.get("name")
            content_item.input = item.get("input")
        elif content_type == "tool_result":
            content_item.tool_use_id = item.get("tool_use_id")
            content_item.output = item.get("output")
            content_item.error = item.get("error")

        items.append(content_item)

    return items


@dataclass
class SubagentInfo:
    """Information about a subagent."""

    agent_id: str
    jsonl_path: Path
    meta_path: Path | None = None
    agent_type: str | None = None
    description: str | None = None


@dataclass
class SessionData:
    """Parsed session data."""

    session_id: str
    jsonl_path: Path
    messages: list[Message] = field(default_factory=list)
    subagents: list[SubagentInfo] = field(default_factory=list)
    agent_type: str | None = None
    agent_description: str | None = None


def parse_jsonl(jsonl_path: Path) -> list[Message]:
    """Parse a JSONL file and return a list of messages."""
    messages = []
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                msg = Message.from_line(line)
                if msg:
                    messages.append(msg)
    except FileNotFoundError:
        return []
    return messages


def discover_subagents(jsonl_path: Path) -> list[SubagentInfo]:
    """Discover subagents associated with a JSONL file."""
    subagents = []

    # Check if there's a directory with the same name
    session_dir = jsonl_path.with_suffix("")
    if not session_dir.is_dir():
        return subagents

    subagents_dir = session_dir / "subagents"
    if not subagents_dir.is_dir():
        return subagents

    # Find all .jsonl files in subagents directory
    for jsonl_file in subagents_dir.glob("*.jsonl"):
        agent_id = jsonl_file.stem  # Remove .jsonl extension

        # Look for corresponding .meta.json file
        meta_file = jsonl_file.with_suffix(".meta.json")
        agent_type = None
        description = None

        if meta_file.exists():
            try:
                with open(meta_file, encoding="utf-8") as f:
                    meta_data = json.load(f)
                    agent_type = meta_data.get("agentType")
                    description = meta_data.get("description")
            except (json.JSONDecodeError, FileNotFoundError):
                pass

        subagents.append(
            SubagentInfo(
                agent_id=agent_id,
                jsonl_path=jsonl_file,
                meta_path=meta_file if meta_file.exists() else None,
                agent_type=agent_type,
                description=description,
            )
        )

    return subagents


def parse_session(jsonl_path: Path) -> SessionData:
    """Parse a session JSONL file and discover subagents."""
    session_id = jsonl_path.stem

    messages = parse_jsonl(jsonl_path)
    subagents = discover_subagents(jsonl_path)

    return SessionData(
        session_id=session_id,
        jsonl_path=jsonl_path,
        messages=messages,
        subagents=subagents,
    )


def parse_subagent(subagent: SubagentInfo) -> SessionData:
    """Parse a subagent JSONL file."""
    messages = parse_jsonl(subagent.jsonl_path)

    return SessionData(
        session_id=subagent.agent_id,
        jsonl_path=subagent.jsonl_path,
        messages=messages,
        agent_type=subagent.agent_type,
        agent_description=subagent.description,
    )
