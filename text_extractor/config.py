"""Configuration for text extractor."""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ToolUseConfig:
    """Configuration for tool_use content filtering."""

    mode: Literal["compact", "full", "exclude"] = "compact"
    include_names: list[str] = field(default_factory=list)
    max_input_length: int = 200


@dataclass
class SubagentConfig:
    """Configuration for subagent processing."""

    include_meta: bool = True
    filter_by_type: list[str] = field(default_factory=list)


@dataclass
class ContentFilterConfig:
    """Configuration for content type filtering."""

    include: list[str] = field(default_factory=lambda: ["text", "thinking", "tool_use"])
    exclude: list[str] = field(default_factory=lambda: ["tool_result"])


@dataclass
class MessageFilterConfig:
    """Configuration for message type filtering."""

    include: list[str] = field(default_factory=lambda: ["user", "assistant"])
    exclude: list[str] = field(default_factory=lambda: ["queue-operation", "file-history-snapshot"])


@dataclass
class FilterConfig:
    """Main filter configuration."""

    message_types: MessageFilterConfig = field(default_factory=MessageFilterConfig)
    content_types: ContentFilterConfig = field(default_factory=ContentFilterConfig)
    tool_use: ToolUseConfig = field(default_factory=ToolUseConfig)
    subagent: SubagentConfig = field(default_factory=SubagentConfig)


DEFAULT_CONFIG = FilterConfig()
