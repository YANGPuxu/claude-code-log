"""Text extraction from parsed messages."""

from __future__ import annotations

from text_extractor.config import FilterConfig
from text_extractor.parser import ContentItem, Message


def extract_text_from_content(item: ContentItem, config: FilterConfig) -> str | None:
    """Extract text from a ContentItem based on configuration."""
    content_type = item.type

    # Check if this content type should be excluded
    if content_type in config.content_types.exclude:
        return None

    # Check if this content type should be included (if include list is non-empty)
    if config.content_types.include and content_type not in config.content_types.include:
        return None

    if content_type == "text":
        return item.text or ""
    elif content_type == "thinking":
        return f"[Thinking]\n{item.text or ''}" if item.text else None
    elif content_type == "tool_use":
        return extract_tool_use_text(item, config.tool_use)
    elif content_type == "tool_result":
        return extract_tool_result_text(item, config)
    return None


def extract_tool_use_text(item: ContentItem, tool_config) -> str | None:
    """Extract text from a tool_use content item."""
    if tool_config.mode == "exclude":
        return None

    name = item.name or "Unknown"

    # Filter by tool name if specified
    if tool_config.include_names and name not in tool_config.include_names:
        return None

    if tool_config.mode == "full":
        input_str = format_input(item.input)
        return f"[Tool: {name}]\n{input_str}"

    # Compact mode
    parts = [f"[Tool: {name}"]

    if item.input:
        # Extract key information from input
        if "file_path" in item.input:
            parts.append(f" file={item.input['file_path']}")
        if "command" in item.input:
            cmd = item.input["command"]
            if len(cmd) > tool_config.max_input_length:
                cmd = cmd[: tool_config.max_input_length] + "..."
            parts.append(f" cmd={cmd}")
        elif "prompt" in item.input:
            prompt = item.input["prompt"]
            if len(prompt) > tool_config.max_input_length:
                prompt = prompt[: tool_config.max_input_length] + "..."
            parts.append(f" prompt={prompt}")

    return "".join(parts) + "]"


def format_input(input_data: dict | None) -> str:
    """Format input data as string."""
    if not input_data:
        return ""
    return str(input_data)


def extract_tool_result_text(item: ContentItem, config: FilterConfig) -> str | None:
    """Extract text from a tool_result content item."""
    # tool_result is typically excluded by default
    if "tool_result" in config.content_types.exclude:
        return None

    output = item.output or item.error
    if output:
        output_str = str(output)
        if len(output_str) > 500:
            output_str = output_str[:500] + "..."
        return f"[Tool Result]\n{output_str}"
    return None


def extract_text_from_message(msg: Message, config: FilterConfig) -> str | None:
    """Extract all text from a message."""
    # Skip if content is empty
    if not msg.content:
        return None

    parts = []
    for item in msg.content:
        text = extract_text_from_content(item, config)
        if text:
            parts.append(text)

    if not parts:
        return None

    return "\n".join(parts)


def extract_message_metadata(msg: Message) -> dict[str, str]:
    """Extract metadata from a message."""
    return {
        "uuid": msg.uuid,
        "type": msg.type,
        "timestamp": msg.timestamp,
    }
