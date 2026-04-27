"""HTML-specific rendering utilities.

This module contains all HTML generation code:
- CSS class computation from message type and modifiers
- Message emoji generation
- HTML escaping and markdown rendering
- Collapsible content rendering
- Tool-specific HTML formatters
- Message content HTML rendering
- Template environment management

The functions here transform format-neutral TemplateMessage data into
HTML-specific output.
"""

import functools
import html
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

import mistune
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .renderer_code import highlight_code_with_pygments, truncate_highlighted_preview
from ..models import (
    AssistantTextMessage,
    BashInputMessage,
    BashOutputMessage,
    CommandOutputMessage,
    CompactedSummaryMessage,
    HookSummaryMessage,
    MessageContent,
    SessionHeaderMessage,
    SlashCommandMessage,
    SystemMessage,
    TaskNotificationMessage,
    TeammateMessage,
    ThinkingMessage,
    ToolResultMessage,
    ToolUseMessage,
    UnknownMessage,
    UserMemoryMessage,
    UserSlashCommandMessage,
    UserSteeringMessage,
    UserTextMessage,
)
from ..renderer_timings import timing_stat

if TYPE_CHECKING:
    from ..renderer import TemplateMessage


# -- CSS Class Registry -------------------------------------------------------
# Maps content types to their CSS classes.
# The first class is typically the base type (user, assistant, system, etc.),
# followed by any static modifiers.

CSS_CLASS_REGISTRY: dict[type[MessageContent], list[str]] = {
    # System message types
    SystemMessage: ["system"],  # level added dynamically
    HookSummaryMessage: ["system", "system-hook"],
    # User message types
    UserTextMessage: ["user"],
    UserSteeringMessage: ["user", "steering"],
    SlashCommandMessage: ["user", "slash-command"],
    UserSlashCommandMessage: ["user", "slash-command"],
    UserMemoryMessage: ["user"],
    CompactedSummaryMessage: ["user", "compacted"],
    CommandOutputMessage: ["user", "command-output"],
    TeammateMessage: ["user", "teammate"],
    # Async-agent <task-notification> entry (issue #90). Carries the
    # `user` class so the runtime "User" filter toggle keeps it visible
    # — without it, the rendered <div> would only carry the
    # `task-notification` modifier, which matches no filter toolbar
    # type and would be permanently flagged `filtered-hidden`. The
    # modifier itself uses hyphens to match the rest of this registry
    # (`system-hook`, `slash-command`, `command-output`, `bash-input`,
    # `bash-output`, `task-notification-card`/`-backlink` in CSS) — the
    # underlying message-type identifier `"task_notification"` (set on
    # `TaskNotificationMessage.message_type`) keeps its underscore.
    TaskNotificationMessage: ["user", "task-notification"],
    # Assistant message types
    AssistantTextMessage: ["assistant"],
    # Tool message types
    ToolUseMessage: ["tool_use"],
    ToolResultMessage: ["tool_result"],  # error added dynamically
    # Other message types
    ThinkingMessage: ["thinking"],
    SessionHeaderMessage: ["session_header"],
    BashInputMessage: ["bash-input"],
    BashOutputMessage: ["bash-output"],
    UnknownMessage: ["unknown"],
}


def _get_css_classes_from_content(content: MessageContent) -> list[str]:
    """Get CSS classes from content type using the registry.

    Walks the MRO to find a matching registry entry, then adds
    any dynamic modifiers based on content attributes.
    """
    for cls in type(content).__mro__:
        if not issubclass(cls, MessageContent):
            continue
        if classes := CSS_CLASS_REGISTRY.get(cls):
            result = list(classes)
            # Dynamic modifiers based on content attributes
            if isinstance(content, SystemMessage):
                result.append(f"system-{content.level}")
            elif isinstance(content, ToolResultMessage) and content.is_error:
                result.append("error")
            return result
    return []


# -- CSS and Message Display --------------------------------------------------


def css_class_from_message(msg: "TemplateMessage") -> str:
    """Generate CSS class string from message type and modifiers.

    Uses CSS_CLASS_REGISTRY to derive classes from content type,
    with fallback to msg.type for messages without registered content.

    The order of classes follows the original pattern:
    1. Message type (from content type or msg.type fallback)
    2. Content-derived modifiers (e.g., slash-command, compacted, error)
    3. Cross-cutting modifier flags: steering, sidechain

    Args:
        msg: The template message to generate CSS classes for

    Returns:
        Space-separated CSS class string (e.g., "user slash-command sidechain")
    """
    # Get base classes and content-derived modifiers from content type
    if msg.content:
        parts = _get_css_classes_from_content(msg.content)
        if not parts:
            parts = [msg.type]  # Fallback if content type not in registry
    else:
        parts = [msg.type]

    # Cross-cutting modifier flags (not derivable from content type alone)
    if msg.is_sidechain:
        parts.append("sidechain")

    return " ".join(parts)


def is_session_header(msg: "TemplateMessage") -> bool:
    """Check if message is a session header.

    Args:
        msg: The template message to check

    Returns:
        True if message content is a SessionHeaderMessage
    """
    return isinstance(msg.content, SessionHeaderMessage)


def get_message_emoji(msg: "TemplateMessage") -> str:
    """Return appropriate emoji for message type.

    Args:
        msg: The template message to get emoji for

    Returns:
        Emoji string for the message type, or empty string if no emoji
    """
    msg_type = msg.type

    if msg_type == "session_header":
        return "📋"
    elif msg_type == "user":
        # Command output has no emoji (neutral - can be from built-in or user command)
        if isinstance(msg.content, CommandOutputMessage):
            return ""
        return "🤷"
    elif msg_type == "bash-input":
        return "💻"
    elif msg_type == "assistant":
        if msg.is_sidechain:
            return "🔗"
        return "🤖"
    elif msg_type == "system":
        return "⚙️"
    elif msg_type == "tool_use":
        return "🛠️"
    elif msg_type == "tool_result":
        if isinstance(msg.content, ToolResultMessage) and msg.content.is_error:
            return "🚨"
        return "🧰"
    elif msg_type == "thinking":
        return "💭"
    elif msg_type == "image":
        return "🖼️"
    return ""


# -- HTML Utilities -----------------------------------------------------------


def escape_html(text: str) -> str:
    """Escape HTML special characters in text.

    Also normalizes line endings (CRLF -> LF) to prevent double spacing in <pre> blocks.
    """
    # Normalize CRLF to LF to prevent double line breaks in HTML
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return html.escape(normalized)


def _create_pygments_plugin() -> Any:
    """Create a mistune plugin that uses Pygments for code block syntax highlighting."""
    from pygments import highlight
    from pygments.lexer import Lexer
    from pygments.lexers import get_lexer_by_name, TextLexer
    from pygments.formatter import Formatter
    from pygments.formatters import HtmlFormatter
    from pygments.util import ClassNotFound

    def plugin_pygments(md: Any) -> None:
        """Plugin to add Pygments syntax highlighting to code blocks."""
        original_render = md.renderer.block_code

        def block_code(code: str, info: Optional[str] = None) -> str:
            """Render code block with Pygments syntax highlighting if language is specified."""
            if info:
                # Language hint provided, use Pygments
                lang = info.split()[0] if info else ""
                # Default to plain text lexer
                lexer: Lexer = TextLexer()
                try:
                    lexer = get_lexer_by_name(lang, stripall=False)
                except ClassNotFound:
                    pass  # Already have default

                formatter: Formatter = HtmlFormatter(
                    linenos=False,  # No line numbers in markdown code blocks
                    cssclass="highlight",
                    wrapcode=True,
                )
                # Track Pygments timing if enabled
                with timing_stat("_pygments_timings"):
                    return str(highlight(code, lexer, formatter))
            else:
                # No language hint, use default rendering
                return original_render(code, info)

        md.renderer.block_code = block_code

    return plugin_pygments


@functools.lru_cache(maxsize=1)
def _get_markdown_renderer() -> mistune.Markdown:
    """Get cached Mistune markdown renderer with Pygments syntax highlighting."""
    return mistune.create_markdown(
        plugins=[
            "strikethrough",
            "footnotes",
            "table",
            "url",
            "task_lists",
            "def_list",
            _create_pygments_plugin(),
        ],
        escape=False,  # Don't escape HTML since we want to render markdown properly
        hard_wrap=True,  # Line break for newlines (checklists in Assistant messages)
    )


def render_markdown(text: str) -> str:
    """Convert markdown text to HTML using mistune with Pygments syntax highlighting."""
    # Track markdown rendering time if enabled
    with timing_stat("_markdown_timings"):
        renderer = _get_markdown_renderer()
        return str(renderer(text))


@functools.lru_cache(maxsize=1)
def _get_user_markdown_renderer() -> mistune.Markdown:
    """Markdown renderer for user-authored text.

    Differs from the shared renderer in one critical way: ``escape=True``
    so a user typing raw ``<script>`` or other HTML sees the literal
    characters rendered as code, not injected into the DOM. Assistant
    content uses ``escape=False`` deliberately (tool output renders
    pre-formed HTML); user content must not bypass escaping.
    """
    return mistune.create_markdown(
        plugins=[
            "strikethrough",
            "footnotes",
            "table",
            "url",
            "task_lists",
            "def_list",
            _create_pygments_plugin(),
        ],
        escape=True,
        hard_wrap=True,
    )


def render_user_markdown(text: str) -> str:
    """Render user-authored Markdown with HTML escaping enabled."""
    with timing_stat("_markdown_timings"):
        renderer = _get_user_markdown_renderer()
        return str(renderer(text))


# HTML tags that are self-closing / void per the HTML spec — these do
# not appear on the open-tag stack in `is_well_formed_html`.
_VOID_HTML_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


def is_well_formed_html(fragment: str) -> bool:
    """Check that an HTML fragment has balanced open/close tags.

    Uses stdlib `html.parser.HTMLParser` to walk the fragment, tracking
    an open-tag stack. Returns False if:

    - A close tag appears with no matching open tag on the stack.
    - Tags remain open at end of input.
    - The parser raises (malformed start tag, bad attribute quoting, etc.).

    Void elements (``<br>``, ``<img>``, …) don't push to the stack, and
    XHTML-style self-closing syntax (``<br />``, ``<hr />``) is treated
    as equivalent — mistune emits XHTML self-closing for voids so the
    check must accept both.
    """
    from html.parser import HTMLParser

    stack: list[str] = []
    errors: list[str] = []

    class _Checker(HTMLParser):
        def handle_starttag(
            self, tag: str, attrs: list[tuple[str, Optional[str]]]
        ) -> None:
            if tag not in _VOID_HTML_TAGS:
                stack.append(tag)

        def handle_endtag(self, tag: str) -> None:
            if not stack:
                errors.append(f"unexpected </{tag}>")
                return
            if stack[-1] != tag:
                errors.append(
                    f"mismatched close: expected </{stack[-1]}>, got </{tag}>"
                )
                return
            stack.pop()

        def handle_startendtag(
            self, tag: str, attrs: list[tuple[str, Optional[str]]]
        ) -> None:
            # ``<tag />`` — XHTML self-closing. The element opens and
            # closes in one token, so neither push nor pop is needed.
            # Don't fall through to the default implementation, which
            # calls `handle_starttag` + `handle_endtag` and would make
            # a non-void `<br />` look unbalanced.
            return

        def error(
            self, message: str
        ) -> None:  # pragma: no cover — stdlib subclasses don't call this in 3.10+
            errors.append(message)

    parser = _Checker(convert_charrefs=True)
    try:
        parser.feed(fragment)
        parser.close()
    except Exception as exc:  # parser itself raised — treat as ill-formed
        errors.append(str(exc))

    return not errors and not stack


# -- Collapsible Content Rendering --------------------------------------------


def render_collapsible_code(
    preview_html: str,
    full_html: str,
    line_count: int,
    is_markdown: bool = False,
) -> str:
    """Render a collapsible code/content block with preview.

    Creates a details element with a line count badge and preview content
    that expands to show the full content.

    Args:
        preview_html: HTML content to show in the collapsed summary
        full_html: HTML content to show when expanded
        line_count: Number of lines (shown in the badge)
        is_markdown: If True, adds 'markdown' class to preview and full content divs

    Returns:
        HTML string with collapsible details element
    """
    markdown_class = " markdown" if is_markdown else ""
    return f"""<details class='collapsible-code'>
        <summary>
            <span class='line-count'>{line_count} lines</span>
            <div class='preview-content{markdown_class}'>{preview_html}</div>
        </summary>
        <div class='code-full{markdown_class}'>{full_html}</div>
    </details>"""


def render_markdown_collapsible(
    raw_content: str,
    css_class: str,
    line_threshold: int = 20,
    preview_line_count: int = 5,
) -> str:
    """Render markdown content, making it collapsible if it exceeds a line threshold.

    For long content, creates a collapsible details element with a preview.
    For short content, renders inline with the specified CSS class.

    Args:
        raw_content: The raw text content to render as markdown
        css_class: CSS class for the wrapper div (e.g., "task-prompt", "task-result")
        line_threshold: Number of lines above which content becomes collapsible (default 20)
        preview_line_count: Number of lines to show in the preview (default 5)

    Returns:
        HTML string with rendered markdown, optionally wrapped in collapsible details
    """
    rendered_html = render_markdown(raw_content)

    lines = raw_content.splitlines()
    if len(lines) <= line_threshold:
        # Short content, show inline
        return f'<div class="{css_class} markdown">{rendered_html}</div>'

    # Long content - make collapsible with rendered preview
    preview_lines = lines[:preview_line_count]
    preview_text = "\n".join(preview_lines)
    if len(lines) > preview_line_count:
        preview_text += "\n\n..."
    # Render truncated markdown (produces valid HTML with proper tag closure)
    preview_html = render_markdown(preview_text)

    collapsible = render_collapsible_code(
        preview_html, rendered_html, len(lines), is_markdown=True
    )
    return f'<div class="{css_class}">{collapsible}</div>'


def render_file_content_collapsible(
    code_content: str,
    file_path: str,
    css_class: str,
    linenostart: int = 1,
    line_threshold: int = 12,
    preview_line_count: int = 5,
    suffix_html: str = "",
) -> str:
    """Render file content with syntax highlighting, collapsible if long.

    Highlights code using Pygments and wraps in a collapsible details element
    if the content exceeds the line threshold. Uses preview truncation from
    already-highlighted HTML to avoid double Pygments calls.

    Args:
        code_content: The raw code content to highlight
        file_path: File path for syntax detection (extension-based)
        css_class: CSS class for the wrapper div (e.g., 'write-tool-content')
        linenostart: Starting line number for Pygments (default 1)
        line_threshold: Number of lines above which content becomes collapsible
        preview_line_count: Number of lines to show in the preview
        suffix_html: Optional HTML to append after the code (inside wrapper div)

    Returns:
        HTML string with highlighted code, collapsible if >line_threshold lines
    """
    # Highlight code with Pygments (single call)
    highlighted_html = highlight_code_with_pygments(
        code_content, file_path, linenostart=linenostart
    )

    html_parts = [f"<div class='{css_class}'>"]

    lines = code_content.splitlines()
    if len(lines) > line_threshold:
        # Extract preview from already-highlighted HTML (avoids double highlighting)
        preview_html = truncate_highlighted_preview(
            highlighted_html, preview_line_count
        )
        html_parts.append(
            render_collapsible_code(preview_html, highlighted_html, len(lines))
        )
    else:
        # Show directly without collapsible
        html_parts.append(highlighted_html)

    if suffix_html:
        html_parts.append(suffix_html)

    html_parts.append("</div>")
    return "".join(html_parts)


# -- Template Environment -----------------------------------------------------


def starts_with_emoji(text: str) -> bool:
    """Check if a string starts with an emoji character.

    Checks common emoji Unicode ranges:
    - Emoticons: U+1F600 - U+1F64F
    - Misc Symbols and Pictographs: U+1F300 - U+1F5FF
    - Transport and Map Symbols: U+1F680 - U+1F6FF
    - Supplemental Symbols: U+1F900 - U+1F9FF
    - Misc Symbols: U+2600 - U+26FF
    - Dingbats: U+2700 - U+27BF
    """
    if not text:
        return False

    first_char = text[0]
    code_point = ord(first_char)

    return (
        0x1F600 <= code_point <= 0x1F64F  # Emoticons
        or 0x1F300 <= code_point <= 0x1F5FF  # Misc Symbols and Pictographs
        or 0x1F680 <= code_point <= 0x1F6FF  # Transport and Map Symbols
        or 0x1F900 <= code_point <= 0x1F9FF  # Supplemental Symbols
        or 0x2600 <= code_point <= 0x26FF  # Misc Symbols
        or 0x2700 <= code_point <= 0x27BF  # Dingbats
    )


@functools.lru_cache(maxsize=1)
def get_template_environment() -> Environment:
    """Get cached Jinja2 template environment for HTML rendering.

    Creates a Jinja2 environment configured with:
    - Template loading from the templates directory
    - HTML auto-escaping
    - Custom template filters/functions (starts_with_emoji)

    Returns:
        Configured Jinja2 Environment (cached after first call)
    """
    templates_dir = Path(__file__).parent / "templates"
    env = Environment(
        loader=FileSystemLoader(templates_dir),
        autoescape=select_autoescape(["html", "xml"]),
    )
    # Add custom filters/functions
    # Cast to Any to bypass Jinja2's overly strict globals type
    globals_dict: Any = env.globals
    globals_dict["starts_with_emoji"] = starts_with_emoji
    return env
