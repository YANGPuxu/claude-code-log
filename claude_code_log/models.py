"""Pydantic models for Claude Code transcript JSON structures."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Union, Optional, Literal

from pydantic import BaseModel, Field


class MessageType(str, Enum):
    """Primary message type classification.

    This enum covers both JSONL entry types and rendering types.
    Using str as base class maintains backward compatibility with string comparisons.

    JSONL Entry Types (from transcript files):
    - USER, ASSISTANT, SYSTEM, SUMMARY, QUEUE_OPERATION

    Rendering Types (derived during processing):
    - TOOL_USE, TOOL_RESULT, THINKING, IMAGE
    - BASH_INPUT, BASH_OUTPUT
    - SESSION_HEADER, UNKNOWN
    """

    # JSONL entry types
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    SUMMARY = "summary"
    QUEUE_OPERATION = "queue-operation"

    # Rendering/display types (derived from content)
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    THINKING = "thinking"
    IMAGE = "image"
    BASH_INPUT = "bash-input"
    BASH_OUTPUT = "bash-output"
    SESSION_HEADER = "session-header"
    UNKNOWN = "unknown"

    # System subtypes (for css_class)
    SYSTEM_INFO = "system-info"
    SYSTEM_WARNING = "system-warning"
    SYSTEM_ERROR = "system-error"


class DetailLevel(str, Enum):
    """Output detail level controlling which message types are included.

    Levels form a hierarchy: full > high > low > minimal.
    """

    FULL = "full"  # Everything
    HIGH = "high"  # Detailed but cleaned (no system/hook noise)
    LOW = "low"  # Interaction-focused + key signals
    MINIMAL = "minimal"  # User + assistant messages only


# =============================================================================
# JSONL Content Models (Pydantic)
# =============================================================================
# Low-level content types parsed from JSONL transcript entries.
# These are defined first as they're the "input" types from transcript files.


class TextContent(BaseModel):
    """Text content block within a message content array."""

    type: Literal["text"]
    text: str


class ImageSource(BaseModel):
    """Base64-encoded image source data."""

    type: Literal["base64"]
    media_type: str
    data: str


class ImageContent(BaseModel):
    """Image content.

    This represents an image within a content array, not a standalone message.
    Images are always part of UserTextMessage.items or AssistantTextMessage.items.
    """

    type: Literal["image"]
    source: ImageSource


class UsageInfo(BaseModel):
    """Token usage information for tracking API consumption."""

    input_tokens: Optional[int] = None
    cache_creation_input_tokens: Optional[int] = None
    cache_read_input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    service_tier: Optional[str] = None
    server_tool_use: Optional[dict[str, Any]] = None


class ToolUseContent(BaseModel):
    type: Literal["tool_use"]
    id: str
    name: str
    input: dict[str, Any]


class ToolResultContent(BaseModel):
    type: Literal["tool_result"]
    tool_use_id: str
    content: Union[str, list[dict[str, Any]]]
    is_error: Optional[bool] = None
    agentId: Optional[str] = None  # Reference to agent file for sub-agent messages


class ThinkingContent(BaseModel):
    type: Literal["thinking"]
    thinking: str
    signature: Optional[str] = None


# Content item types that appear in message content arrays
ContentItem = Union[
    TextContent,
    ToolUseContent,
    ToolResultContent,
    ThinkingContent,
    ImageContent,
]


class UserMessageModel(BaseModel):
    role: Literal["user"]
    content: list[ContentItem]
    usage: Optional["UsageInfo"] = (
        None  # For type compatibility with AssistantMessageModel
    )


class AssistantMessageModel(BaseModel):
    """Assistant message model."""

    id: str
    type: Literal["message"]
    role: Literal["assistant"]
    model: str
    content: list[ContentItem]
    stop_reason: Optional[str] = None
    stop_sequence: Optional[str] = None
    usage: Optional[UsageInfo] = None


# Tool result type - flexible to accept various result formats from JSONL
# The specific parsing/formatting happens in tool_formatters.py using
# ReadOutput, EditOutput, etc. (see Tool Output Content Models section)
ToolUseResult = Union[
    str,
    list[Any],  # Covers list[TodoWriteItem], list[ContentItem], etc.
    dict[str, Any],  # Covers structured results
]


class BaseTranscriptEntry(BaseModel):
    parentUuid: Optional[str]
    isSidechain: bool
    userType: str
    cwd: str
    sessionId: str
    version: str
    uuid: str
    timestamp: str
    isMeta: Optional[bool] = None
    agentId: Optional[str] = None  # Agent ID for sidechain messages
    gitBranch: Optional[str] = None  # Git branch name when available
    teamName: Optional[str] = None  # Active team name (teammates feature)


class UserTranscriptEntry(BaseTranscriptEntry):
    type: Literal["user"]
    message: UserMessageModel
    toolUseResult: Optional[ToolUseResult] = None
    agentId: Optional[str] = None  # From toolUseResult when present
    # Present on isMeta=True entries produced by a Skill tool invocation —
    # carries the id of the originating tool_use so the renderer can fold
    # the skill body into that tool_use block. See issue #93.
    sourceToolUseID: Optional[str] = None


class AssistantTranscriptEntry(BaseTranscriptEntry):
    type: Literal["assistant"]
    message: AssistantMessageModel
    requestId: Optional[str] = None


class SummaryTranscriptEntry(BaseModel):
    type: Literal["summary"]
    summary: str
    leafUuid: str
    cwd: Optional[str] = None
    sessionId: None = None  # Summaries don't have a sessionId


class SystemTranscriptEntry(BaseTranscriptEntry):
    """System messages like warnings, notifications, hook summaries, etc."""

    type: Literal["system"]
    content: Optional[str] = None
    subtype: Optional[str] = None  # e.g., "stop_hook_summary"
    level: Optional[str] = None  # e.g., "warning", "info", "error"
    # Hook summary fields (for subtype="stop_hook_summary")
    hasOutput: Optional[bool] = None
    hookErrors: Optional[list[str]] = None
    hookInfos: Optional[list[dict[str, Any]]] = None
    preventedContinuation: Optional[bool] = None
    # Compact boundary fields (for subtype="compact_boundary"); primarily
    # `preTokens`, `trigger`, `postTokens`, `durationMs`. Read at factory
    # time into SystemMessage.compact_pre_tokens / compact_trigger.
    compactMetadata: Optional[dict[str, Any]] = None


class QueueOperationTranscriptEntry(BaseModel):
    """Queue operations (enqueue/dequeue/remove) for message queueing tracking.

    enqueue/dequeue are internal operations that track when messages are queued and dequeued.
    They are parsed but not rendered, as the content duplicates actual user messages.

    'remove' operations are out-of-band user inputs made visible to the agent while working
    for "steering" purposes. These should be rendered as user messages with a 'steering' CSS class.
    Content can be a list of ContentItems or a simple string (for 'remove' operations).
    """

    type: Literal["queue-operation"]
    operation: Literal["enqueue", "dequeue", "remove", "popAll"]
    timestamp: str
    sessionId: str
    content: Optional[Union[list[ContentItem], str]] = (
        None  # List for enqueue, str for remove/popAll
    )


class PassthroughTranscriptEntry(BaseModel):
    """Structural-only entry for DAG chain continuity.

    Captures entries like "attachment", "permission-mode", etc. that have
    uuid/parentUuid and participate in the DAG chain but are not rendered.
    Without these, messages whose parentUuid points to a dropped entry
    become false roots in the DAG.
    """

    uuid: str
    parentUuid: Optional[str] = None
    sessionId: str
    timestamp: str
    type: Optional[str] = None  # Original type (e.g. "attachment")
    isSidechain: bool = False
    agentId: Optional[str] = None


TranscriptEntry = Union[
    UserTranscriptEntry,
    AssistantTranscriptEntry,
    SummaryTranscriptEntry,
    SystemTranscriptEntry,
    QueueOperationTranscriptEntry,
    PassthroughTranscriptEntry,
]


# =============================================================================
# Message Metadata
# =============================================================================
# Common metadata fields extracted from transcript entries.


@dataclass
class MessageMeta:
    """Common metadata extracted from transcript entries.

    These fields are shared across all message types and are used to create
    the TemplateMessage wrapper for rendering.

    Note: formatted_timestamp is computed at render time, not stored here.
    """

    # Identity fields
    session_id: str
    timestamp: str  # Raw ISO timestamp
    uuid: str
    parent_uuid: Optional[str] = None

    # Context fields
    is_sidechain: bool = False
    is_meta: bool = False  # User slash command (isMeta=True in transcript)
    source_tool_use_id: Optional[str] = (
        None  # Skill pairing (see UserTranscriptEntry.sourceToolUseID)
    )
    agent_id: Optional[str] = None
    cwd: str = ""
    git_branch: Optional[str] = None
    team_name: Optional[str] = None  # Active team name (teammates feature)

    @classmethod
    def empty(cls, uuid: str = "") -> "MessageMeta":
        """Create a placeholder MessageMeta with empty/default values.

        Useful for cases where full metadata isn't available at creation time
        (e.g., SummaryTranscriptEntry where session_id is matched later).
        """
        return cls(session_id="", timestamp="", uuid=uuid)


# =============================================================================
# Message Content Models
# =============================================================================
# Structured content models for format-neutral message representation.
# These replace the direct HTML generation in renderer.py, allowing different
# renderers (HTML, text, etc.) to format the content appropriately.


@dataclass
class MessageContent:
    """Base class for structured message content.

    Subclasses represent specific content types that renderers can format
    appropriately for their output format.

    The `meta` field is required and first positional, ensuring all message
    content always has associated metadata. Use MessageMeta.empty() when
    full metadata isn't available at creation time.

    Note: Render-time relationship data (pairing, hierarchy, children) is stored
    on TemplateMessage, not here. MessageContent is pure transcript data,
    except for message_index which links back to TemplateMessage for render-time
    lookups (e.g., accessing paired messages).
    """

    meta: MessageMeta

    # Set by RenderingContext.register() to enable content→TemplateMessage lookup
    # Using init=False to avoid dataclass inheritance issues with required fields
    message_index: Optional[int] = field(default=None, init=False, repr=False)

    @property
    def message_type(self) -> str:
        """Return the message type identifier for this content.

        Subclasses MUST override this to return their specific type.
        This is used for CSS classes, filtering, and type-based rendering.
        """
        raise NotImplementedError("Subclasses must implement message_type property")

    @property
    def has_markdown(self) -> bool:
        """Whether this content should be rendered as markdown.

        Subclasses that contain markdown content should override to return True.
        """
        return False


@dataclass
class SystemMessage(MessageContent):
    """System message with level indicator.

    Used for info, warning, and error system messages.
    """

    level: str  # "info", "warning", "error"
    text: str  # Raw text content (may contain ANSI codes)
    # Populated only for subtype="compact_boundary" entries: the token
    # count before compaction and what triggered it ("manual"/"auto").
    # Surfaced in the nav landmark label for /compact points.
    compact_pre_tokens: Optional[int] = None
    compact_trigger: Optional[str] = None

    @property
    def message_type(self) -> str:
        return "system"


@dataclass
class HookInfo:
    """Information about a single hook execution."""

    command: str
    # Could add more fields like exit_code, duration, etc.


@dataclass
class HookSummaryMessage(MessageContent):
    """Hook execution summary.

    Used for subtype="stop_hook_summary" system messages.
    """

    has_output: bool
    hook_errors: list[str]  # Error messages from hooks
    hook_infos: list[HookInfo]  # Info about each hook executed

    @property
    def message_type(self) -> str:
        return "system"


# =============================================================================
# User Message Content Models
# =============================================================================
# Structured content models for user message variants.
# These classify user text based on flags and tag patterns.


@dataclass
class SlashCommandMessage(MessageContent):
    """Content for slash command invocations (e.g., /context, /model).

    These are user messages containing command-name, command-args, and
    command-contents tags parsed from the text.
    """

    command_name: str
    command_args: str
    command_contents: str

    @property
    def message_type(self) -> str:
        return "user"


@dataclass
class CommandOutputMessage(MessageContent):
    """Content for local command output (e.g., output from /context).

    These are user messages containing local-command-stdout tags.
    """

    stdout: str
    is_markdown: bool  # True if content appears to be markdown

    @property
    def message_type(self) -> str:
        return "user"


@dataclass
class BashInputMessage(MessageContent):
    """Content for inline bash commands in user messages.

    These are user messages containing bash-input tags.
    """

    command: str

    @property
    def message_type(self) -> str:
        return "bash-input"


@dataclass
class BashOutputMessage(MessageContent):
    """Content for bash command output.

    These are user messages containing bash-stdout and/or bash-stderr tags.
    """

    stdout: Optional[str] = None  # Raw stdout content (may contain ANSI codes)
    stderr: Optional[str] = None  # Raw stderr content (may contain ANSI codes)

    @property
    def message_type(self) -> str:
        return "bash-output"


# Note: ToolResultMessage and ToolUseMessage are defined in the
# "Tool Message Models" section (before Tool Input Models).


@dataclass
class CompactedSummaryMessage(MessageContent):
    """Content for compacted session summaries.

    These are user messages that contain previous conversation context
    in a compacted format when sessions run out of context.
    Parsed by parse_compacted_summary() in parser.py, formatted by
    format_compacted_summary_content() in html/user_formatters.py.
    """

    summary_text: str

    @property
    def message_type(self) -> str:
        return "user"

    @property
    def has_markdown(self) -> bool:
        return True


@dataclass
class UserMemoryMessage(MessageContent):
    """Content for user memory input.

    These are user messages containing user-memory-input tags.
    Parsed by parse_user_memory() in parser.py, formatted by
    format_user_memory_content() in html/user_formatters.py.
    """

    memory_text: str

    @property
    def message_type(self) -> str:
        return "user"


@dataclass
class UserSlashCommandMessage(MessageContent):
    """Content for slash command expanded prompts (isMeta=True).

    These are LLM-generated instruction text from slash commands.
    The text is markdown formatted and rendered as such.
    Formatted by format_user_slash_command_content() in html/user_formatters.py.
    """

    text: str

    @property
    def message_type(self) -> str:
        return "user"


@dataclass
class IdeOpenedFile:
    """IDE notification for an opened file."""

    content: str  # Raw content from the tag


@dataclass
class IdeSelection:
    """IDE notification for a code selection."""

    content: str  # Raw selection content


@dataclass
class IdeDiagnostic:
    """IDE diagnostic notification.

    Contains either parsed JSON diagnostics or raw content if parsing failed.
    """

    diagnostics: Optional[list[dict[str, Any]]] = None  # Parsed diagnostic objects
    raw_content: Optional[str] = None  # Fallback if JSON parsing failed


@dataclass
class IdeNotificationContent:
    """Content for IDE notification tags (embedded within user messages).

    This is NOT a MessageContent subclass - it's used as an item within
    UserTextMessage.items alongside TextContent and ImageContent.

    Represents IDE notification tags like:
    - <ide_opened_file>: File open notifications
    - <ide_selection>: Code selection notifications
    - <post-tool-use-hook><ide_diagnostics>: Diagnostic JSON arrays

    Format-neutral: stores structured data, not HTML.
    """

    opened_files: list[IdeOpenedFile]
    selections: list[IdeSelection]
    diagnostics: list[IdeDiagnostic]
    remaining_text: str  # Text after notifications extracted


@dataclass
class UserTextMessage(MessageContent):
    """Content for user text with interleaved images and IDE notifications.

    The `items` field preserves the original order of content:
    - TextContent: Text portions of the message
    - ImageContent: Inline images
    - IdeNotificationContent: IDE notification tags (extracted from text)

    Empty items list indicates no content.
    """

    # Interleaved content items preserving original order
    items: list[  # pyright: ignore[reportUnknownVariableType]
        TextContent | ImageContent | IdeNotificationContent
    ] = field(default_factory=list)

    @property
    def message_type(self) -> str:
        return "user"


@dataclass
class UserSteeringMessage(UserTextMessage):
    """Content for user steering prompts (queue-operation "remove").

    These are user messages that steer the conversation by removing
    items from the queue. Inherits from UserTextMessage.
    """

    pass


@dataclass
class TeammateMessageBlock:
    """A single <teammate-message> block extracted from a User entry.

    `is_system=True` corresponds to blocks with teammate_id="system"
    (e.g. teammate_terminated notifications).

    This is a plain data container — the renderer-facing MessageContent
    wrapper is TeammateMessage (which holds one or more of these blocks).
    """

    teammate_id: str
    body: str
    color: Optional[str] = None
    summary: Optional[str] = None
    is_system: bool = False


@dataclass
class TeammateMessage(MessageContent):
    """Content for one or more <teammate-message> blocks in a single User entry.

    Teammate messages are emitted when the experimental teammates feature
    (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`) is active. A single User
    entry may carry several `<teammate-message>` blocks — potentially from
    different teammates — so the factory groups them into `blocks` and
    preserves any non-matching surrounding text in `leading_text` /
    `trailing_text`. The renderer decides whether to present each block
    as its own card or merge them into one.
    """

    blocks: list[TeammateMessageBlock] = field(
        default_factory=lambda: list[TeammateMessageBlock]()
    )
    leading_text: Optional[str] = None
    trailing_text: Optional[str] = None

    @property
    def message_type(self) -> str:
        return "teammate"

    @property
    def has_markdown(self) -> bool:
        return True


# =============================================================================
# Assistant Message Content Models
# =============================================================================
# Structured content models for assistant message variants.
# These classify assistant message parts for format-neutral rendering.


@dataclass
class AssistantTextMessage(MessageContent):
    """Content for assistant text messages with interleaved images.

    These are the text portions of assistant messages that get
    rendered as markdown with syntax highlighting.

    The `items` field preserves the original order of content:
    - TextContent: Text portions of the message
    - ImageContent: Inline images

    Empty items list indicates no content.
    """

    # Interleaved content items preserving original order
    items: list[  # pyright: ignore[reportUnknownVariableType]
        TextContent | ImageContent
    ] = field(default_factory=list)

    # Token usage string (formatted from UsageInfo when available)
    token_usage: Optional[str] = None

    @property
    def message_type(self) -> str:
        return "assistant"

    @property
    def has_markdown(self) -> bool:
        return True


@dataclass
class ThinkingMessage(MessageContent):
    """Message for assistant thinking/reasoning blocks.

    These are the <thinking> blocks that show the assistant's
    internal reasoning process.

    Note: This is distinct from ThinkingContent (the Pydantic model
    for parsing JSONL). This dataclass is for rendering purposes.
    """

    thinking: str
    signature: Optional[str] = None

    # Token usage string (formatted from UsageInfo when available)
    token_usage: Optional[str] = None

    @property
    def message_type(self) -> str:
        return "thinking"

    @property
    def has_markdown(self) -> bool:
        return True


# Note: ToolUseMessage is also an assistant content type, defined in
# "Tool Message Models" section (before Tool Input Models).


@dataclass
class UnknownMessage(MessageContent):
    """Content for unknown/unrecognized content types.

    Used as a fallback when encountering content types that don't have
    specific handlers. Stores the type name for display purposes.
    """

    type_name: str  # The name/description of the unknown type

    @property
    def message_type(self) -> str:
        return "unknown"


# =============================================================================
# Renderer Content Models
# =============================================================================
# Structured content models for renderer-specific elements.
# These are used by the HTML renderer but represent format-neutral data.


@dataclass
class SessionHeaderMessage(MessageContent):
    """Content for session headers in transcript rendering.

    Represents the header displayed at the start of each session
    with session title and optional summary. Includes hierarchy
    fields for parent/child session relationships.
    """

    title: str
    session_id: str
    summary: Optional[str] = None
    parent_session_id: Optional[str] = None
    parent_session_summary: Optional[str] = None
    parent_message_index: Optional[int] = None  # d-{N} index for backlink
    depth: int = 0  # 0 = root, 1 = child, etc.
    attachment_uuid: Optional[str] = None
    is_branch: bool = False  # True for within-session fork branches
    original_session_id: Optional[str] = None  # Original session_id before fork split
    first_uuid: Optional[str] = None  # First UUID in this branch (for forward links)
    # Teammates feature — set when the session was active in a team. Sourced
    # from the first non-None ``teamName`` of any entry in the session.
    team_name: Optional[str] = None

    @property
    def message_type(self) -> str:
        return "session_header"


# =============================================================================
# Tool Message Models
# =============================================================================
# High-level message wrappers for tool invocations and results.
# These wrap the specialized Tool Input/Output models for rendering.


@dataclass
class ToolResultMessage(MessageContent):
    """Message for tool results with rendering context.

    Wraps ToolResultContent or specialized output with additional context
    needed for rendering, such as the associated tool name and file path.
    """

    tool_use_id: str
    output: (
        "ToolOutput"  # Specialized (ReadOutput, etc.) or generic (ToolResultContent)
    )
    is_error: bool = False
    tool_name: Optional[str] = None  # Name of the tool that produced this result
    file_path: Optional[str] = None  # File path for Read/Edit/Write tools

    @property
    def message_type(self) -> str:
        return "tool_result"

    @property
    def has_markdown(self) -> bool:
        """TaskOutput results contain markdown (agent responses)."""
        return isinstance(self.output, TaskOutput)


@dataclass
class ToolUseMessage(MessageContent):
    """Message for tool invocations.

    Wraps ToolUseContent with the parsed input for specialized formatting.
    Falls back to the original ToolUseContent when no specialized parser exists.
    """

    input: "ToolInput"  # Specialized (BashInput, etc.) or ToolUseContent fallback
    tool_use_id: str  # From ToolUseContent.id
    tool_name: str  # From ToolUseContent.name
    # Skill-tool pairing (issue #93): when the Skill tool is invoked,
    # the expanded slash-command body shipped as a separate isMeta=True
    # user entry is folded into this field so the Skill tool_use renders
    # as a single visual unit.
    skill_body: Optional[str] = None

    @property
    def message_type(self) -> str:
        return "tool_use"


# =============================================================================
# Tool Input Models
# =============================================================================
# Typed models for tool inputs.
# These provide type safety and IDE autocompletion for tool parameters.


class BashInput(BaseModel):
    """Input parameters for the Bash tool."""

    command: str
    description: Optional[str] = None
    timeout: Optional[int] = None
    run_in_background: Optional[bool] = None
    dangerouslyDisableSandbox: Optional[bool] = None


class ReadInput(BaseModel):
    """Input parameters for the Read tool."""

    file_path: str
    offset: Optional[int] = None
    limit: Optional[int] = None


class WriteInput(BaseModel):
    """Input parameters for the Write tool."""

    file_path: str
    content: str


class EditInput(BaseModel):
    """Input parameters for the Edit tool."""

    file_path: str
    old_string: str
    new_string: str
    replace_all: Optional[bool] = None


class EditItem(BaseModel):
    """Single edit item for MultiEdit tool."""

    old_string: str
    new_string: str


class MultiEditInput(BaseModel):
    """Input parameters for the MultiEdit tool."""

    file_path: str
    edits: list[EditItem]


class GlobInput(BaseModel):
    """Input parameters for the Glob tool."""

    pattern: str
    path: Optional[str] = None


class GrepInput(BaseModel):
    """Input parameters for the Grep tool.

    Note: Extra fields like -A, -B, -C are allowed for flexibility.
    """

    pattern: str
    path: Optional[str] = None
    glob: Optional[str] = None
    type: Optional[str] = None
    output_mode: Optional[Literal["content", "files_with_matches", "count"]] = None
    multiline: Optional[bool] = None
    head_limit: Optional[int] = None
    offset: Optional[int] = None

    model_config = {"extra": "allow"}  # Allow -A, -B, -C, -i, -n fields


class TaskInput(BaseModel):
    """Input parameters for the Task tool."""

    prompt: str
    subagent_type: str
    description: str
    model: Optional[Literal["sonnet", "opus", "haiku"]] = None
    run_in_background: Optional[bool] = None
    resume: Optional[str] = None
    # Teammate-spawned Task fields (CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1).
    # Populated when the team-lead spawns a named teammate via Task.
    team_name: Optional[str] = None
    name: Optional[str] = None
    mode: Optional[str] = None


class TodoWriteItem(BaseModel):
    """Single todo item for TodoWrite tool (input format).

    All fields have defaults for lenient parsing of legacy/malformed data.
    """

    content: str = ""
    status: str = "pending"  # Allow any string, not just Literal, for flexibility
    activeForm: str = ""
    id: Optional[str] = None
    priority: Optional[str] = None  # Allow any string for flexibility


class TodoWriteInput(BaseModel):
    """Input parameters for the TodoWrite tool."""

    todos: list[TodoWriteItem]


class AskUserQuestionOption(BaseModel):
    """Option for an AskUserQuestion question.

    All fields have defaults for lenient parsing.
    """

    label: str = ""
    description: Optional[str] = None


class AskUserQuestionItem(BaseModel):
    """Single question in AskUserQuestion input.

    All fields have defaults for lenient parsing.
    """

    question: str = ""
    header: Optional[str] = None
    options: list[AskUserQuestionOption] = Field(
        default_factory=lambda: list[AskUserQuestionOption]()
    )
    multiSelect: bool = False


class AskUserQuestionInput(BaseModel):
    """Input parameters for the AskUserQuestion tool.

    Supports both modern format (questions list) and legacy format (single question).
    """

    questions: list[AskUserQuestionItem] = Field(
        default_factory=lambda: list[AskUserQuestionItem]()
    )
    question: Optional[str] = None  # Legacy single question format


class ExitPlanModeInput(BaseModel):
    """Input parameters for the ExitPlanMode tool."""

    plan: str = ""
    launchSwarm: Optional[bool] = None
    teammateCount: Optional[int] = None


class WebSearchInput(BaseModel):
    """Input parameters for the WebSearch tool."""

    query: str


class WebFetchInput(BaseModel):
    """Input parameters for the WebFetch tool."""

    url: str
    prompt: str


# =============================================================================
# Teammates feature tool inputs
# =============================================================================
# CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 surfaces six team-management tools.
# Schemas are lenient (model_config extra="allow") so unknown fields survive.


class TeamCreateInput(BaseModel):
    """Input parameters for the TeamCreate tool."""

    team_name: str = ""
    description: Optional[str] = None
    agent_type: Optional[str] = None

    model_config = {"extra": "allow"}


class TeamDeleteInput(BaseModel):
    """Input parameters for the TeamDelete tool (typically empty)."""

    team_name: Optional[str] = None

    model_config = {"extra": "allow"}


class TaskCreateInput(BaseModel):
    """Input parameters for the TaskCreate tool (teammate task board)."""

    subject: str = ""
    description: Optional[str] = None
    activeForm: Optional[str] = None

    model_config = {"extra": "allow"}


class TaskUpdateInput(BaseModel):
    """Input parameters for the TaskUpdate tool (teammate task board)."""

    taskId: str = ""
    owner: Optional[str] = None
    status: Optional[str] = None

    model_config = {"extra": "allow"}


class TaskListInput(BaseModel):
    """Input parameters for the TaskList tool (empty in practice)."""

    model_config = {"extra": "allow"}


class SendMessageInput(BaseModel):
    """Input parameters for the SendMessage tool (team-lead → teammate)."""

    type: Optional[str] = None
    recipient: Optional[str] = None
    content: str = ""

    model_config = {"extra": "allow"}


# Union of all typed tool inputs
ToolInput = Union[
    BashInput,
    ReadInput,
    WriteInput,
    EditInput,
    MultiEditInput,
    GlobInput,
    GrepInput,
    TaskInput,
    TodoWriteInput,
    AskUserQuestionInput,
    ExitPlanModeInput,
    WebSearchInput,
    WebFetchInput,
    TeamCreateInput,
    TeamDeleteInput,
    TaskCreateInput,
    TaskUpdateInput,
    TaskListInput,
    SendMessageInput,
    ToolUseContent,  # Generic fallback when no specialized parser
]


# =============================================================================
# Tool Output Models
# =============================================================================
# Typed models for tool outputs (symmetric with Tool Input Models).
# These are data containers stored inside ToolResultMessage.output,
# NOT standalone message types (so they don't inherit from MessageContent).


@dataclass
class ReadOutput:
    """Parsed Read tool output.

    Represents the result of reading a file with optional line range.
    Symmetric with ReadInput for tool_use → tool_result pairing.
    """

    file_path: str
    content: str  # File content (may be truncated)
    start_line: int  # 1-based starting line number
    num_lines: int  # Number of lines in content
    total_lines: int  # Total lines in file
    is_truncated: bool  # Whether content was truncated
    system_reminder: Optional[str] = None  # Embedded system reminder text


@dataclass
class WriteOutput:
    """Parsed Write tool output.

    Symmetric with WriteInput for tool_use → tool_result pairing.
    """

    file_path: str
    success: bool
    message: str  # First line acknowledgment (truncated from full output)


@dataclass
class EditDiff:
    """Single diff hunk for edit operations."""

    old_text: str
    new_text: str


@dataclass
class EditOutput:
    """Parsed Edit tool output.

    Contains diff information for file edits.
    Symmetric with EditInput for tool_use → tool_result pairing.
    """

    file_path: str
    success: bool
    diffs: list[EditDiff]  # Changes made
    message: str  # Result message or code snippet
    start_line: int = 1  # Starting line number for code display


@dataclass
class BashOutput:
    """Parsed Bash tool output.

    Symmetric with BashInput for tool_use → tool_result pairing.
    Contains the output with ANSI flag for terminal formatting.
    """

    content: str  # Output content (stdout/stderr combined)
    has_ansi: bool  # True if content contains ANSI escape sequences


@dataclass
class AgentResultMetadata:
    """Structured metadata parsed from the Markdown tail of a Task tool_result.

    Teammate-spawned agents (and async task agents) embed linking and
    accounting info at the end of the agent's response:

        agentId: <id> (use SendMessage with to: '...' to continue this agent)
        worktreePath: /.../worktrees/agent-<id>
        worktreeBranch: worktree-agent-<id>
        <usage>total_tokens: 48421
        tool_uses: 24
        duration_ms: 802753</usage>

    Exposes the parsed fields so the renderer can surface them structurally
    (and so the converter can use `agent_id` to link subagent JSONL files
    back to their spawning Task tool_use).
    """

    agent_id: Optional[str] = None
    worktree_path: Optional[str] = None
    worktree_branch: Optional[str] = None
    total_tokens: Optional[int] = None
    tool_uses: Optional[int] = None
    duration_ms: Optional[int] = None


@dataclass
class TaskOutput:
    """Parsed Task (sub-agent) tool output.

    Symmetric with TaskInput for tool_use → tool_result pairing.
    Contains the agent's final response as markdown. Teammate-spawned tasks
    also populate `metadata` (see AgentResultMetadata) — set by the tool
    factory when the result tail carries `agentId:` / `worktreePath:` / ...
    """

    result: str  # Agent's response (markdown), metadata tail stripped
    metadata: Optional[AgentResultMetadata] = None
    # Teammate-spawn pathway fields. Populated when the Task was spawned for
    # a named teammate; sourced from the tool_use input and/or tool_result.
    teammate_id: Optional[str] = None
    agent_id: Optional[str] = None
    color: Optional[str] = None


@dataclass
class GlobOutput:
    """Parsed Glob tool output.

    Symmetric with GlobInput for tool_use → tool_result pairing.

    TODO: Not currently used - tool results handled as raw strings.
    """

    pattern: str
    files: list[str]  # Matching file paths
    truncated: bool  # Whether list was truncated


@dataclass
class GrepOutput:
    """Parsed Grep tool output.

    Symmetric with GrepInput for tool_use → tool_result pairing.

    TODO: Not currently used - tool results handled as raw strings.
    """

    pattern: str
    matches: list[str]  # Matching lines/files
    output_mode: str  # "content", "files_with_matches", or "count"
    truncated: bool


@dataclass
class AskUserQuestionAnswer:
    """Single Q&A pair from AskUserQuestion result."""

    question: str
    answer: str


@dataclass
class AskUserQuestionOutput:
    """Parsed AskUserQuestion tool output.

    Symmetric with AskUserQuestionInput for tool_use → tool_result pairing.
    Contains the Q&A pairs extracted from the result message.
    """

    answers: list[AskUserQuestionAnswer]  # Q&A pairs
    raw_message: str  # Original message for fallback


@dataclass
class ExitPlanModeOutput:
    """Parsed ExitPlanMode tool output.

    Symmetric with ExitPlanModeInput for tool_use → tool_result pairing.
    Truncates redundant plan echo on success.
    """

    message: str  # Truncated message (without redundant plan)
    approved: bool  # Whether the plan was approved


@dataclass
class WebSearchLink:
    """Single search result link from WebSearch output."""

    title: str
    url: str


@dataclass
class WebSearchOutput:
    """Parsed WebSearch tool output.

    Symmetric with WebSearchInput for tool_use → tool_result pairing.
    Parsed as preamble/links/summary for flexible rendering.
    """

    query: str
    links: list[WebSearchLink]
    preamble: Optional[str] = None  # Text before the Links (usually query header)
    summary: Optional[str] = None  # Markdown analysis after the links


@dataclass
class WebFetchOutput:
    """Parsed WebFetch tool output.

    Symmetric with WebFetchInput for tool_use → tool_result pairing.
    Contains the fetched URL's processed content as markdown.
    """

    url: str  # The URL that was fetched
    result: str  # The processed markdown result
    bytes: Optional[int] = None  # Size of fetched content
    code: Optional[int] = None  # HTTP status code
    code_text: Optional[str] = None  # HTTP status text (e.g., "OK")
    duration_ms: Optional[int] = None  # Time taken in milliseconds


# =============================================================================
# Teammates feature tool outputs
# =============================================================================


@dataclass
class TeamCreateOutput:
    """Parsed TeamCreate tool output."""

    team_name: str
    team_file_path: Optional[str] = None
    lead_agent_id: Optional[str] = None
    raw_text: Optional[str] = None  # Original content if JSON parsing failed


@dataclass
class TeamDeleteOutput:
    """Parsed TeamDelete tool output."""

    success: bool
    message: str = ""
    team_name: Optional[str] = None
    active_members: Optional[list[str]] = None
    raw_text: Optional[str] = None


@dataclass
class TaskCreateOutput:
    """Parsed TaskCreate tool output (teammate task board)."""

    task_id: str
    subject: str = ""
    raw_text: Optional[str] = None


@dataclass
class TaskStatusChange:
    """from → to status transition for TaskUpdate."""

    from_status: Optional[str] = None
    to_status: Optional[str] = None


@dataclass
class TaskUpdateOutput:
    """Parsed TaskUpdate tool output (teammate task board)."""

    success: bool
    task_id: str = ""
    updated_fields: Optional[dict[str, Any]] = None
    status_change: Optional[TaskStatusChange] = None
    raw_text: Optional[str] = None


@dataclass
class TaskListItem:
    """Single task row in TaskList output."""

    id: str
    subject: str = ""
    status: Optional[str] = None
    owner: Optional[str] = None
    blocked_by: Optional[list[str]] = None


@dataclass
class TaskListOutput:
    """Parsed TaskList tool output (teammate task board)."""

    tasks: list[TaskListItem]
    raw_text: Optional[str] = None


@dataclass
class SendMessageOutput:
    """Parsed SendMessage tool output (team-lead → teammate)."""

    success: bool
    message: str = ""
    request_id: Optional[str] = None
    target: Optional[str] = None
    raw_text: Optional[str] = None


# Union of all specialized output types + ToolResultContent as generic fallback
ToolOutput = Union[
    ReadOutput,
    WriteOutput,
    EditOutput,
    BashOutput,
    TaskOutput,
    AskUserQuestionOutput,
    ExitPlanModeOutput,
    WebSearchOutput,
    WebFetchOutput,
    TeamCreateOutput,
    TeamDeleteOutput,
    TaskCreateOutput,
    TaskUpdateOutput,
    TaskListOutput,
    SendMessageOutput,
    # TODO: Add as parsers are implemented:
    # GlobOutput, GrepOutput
    ToolResultContent,  # Generic fallback for unparsed results
]
