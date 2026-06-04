"""Message filtering logic."""

from __future__ import annotations

from text_extractor.config import FilterConfig
from text_extractor.parser import Message, SessionData, SubagentInfo


def should_include_message(msg: Message, config: FilterConfig) -> bool:
    """Determine if a message should be included based on its type."""
    msg_type = msg.type

    # Check exclude list first
    if msg_type in config.message_types.exclude:
        return False

    # Check include list (if non-empty)
    if config.message_types.include:
        return msg_type in config.message_types.include

    return True


def should_include_subagent(subagent: SubagentInfo, config: FilterConfig) -> bool:
    """Determine if a subagent should be included."""
    filter_types = config.subagent.filter_by_type
    if not filter_types:
        return True
    return subagent.agent_type in filter_types if subagent.agent_type else True


def filter_messages(messages: list[Message], config: FilterConfig) -> list[Message]:
    """Filter a list of messages based on configuration."""
    return [msg for msg in messages if should_include_message(msg, config)]


def filter_session(session: SessionData, config: FilterConfig) -> SessionData:
    """Filter messages in a session based on configuration."""
    filtered_messages = filter_messages(session.messages, config)

    # Create a new SessionData with filtered messages
    return SessionData(
        session_id=session.session_id,
        jsonl_path=session.jsonl_path,
        messages=filtered_messages,
        subagents=[
            sa
            for sa in session.subagents
            if should_include_subagent(sa, config)
        ],
        agent_type=session.agent_type,
        agent_description=session.agent_description,
    )
