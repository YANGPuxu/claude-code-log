"""Test cases for Grep tool rendering."""

from claude_code_log.html.tool_formatters import format_grep_input
from claude_code_log.html.renderer import HtmlRenderer
from claude_code_log.markdown.renderer import MarkdownRenderer
from claude_code_log.models import (
    GrepInput,
    MessageMeta,
    ToolUseMessage,
)
from claude_code_log.renderer import TemplateMessage


def _make_grep_message(grep_input: GrepInput) -> TemplateMessage:
    """Create a TemplateMessage wrapping a GrepInput for title tests."""
    content = ToolUseMessage(
        meta=MessageMeta(
            uuid="test-uuid", session_id="test", timestamp="2025-01-01T00:00:00Z"
        ),
        input=grep_input,
        tool_use_id="toolu_test",
        tool_name="Grep",
    )
    return TemplateMessage(content)


class TestGrepInput:
    """Test GrepInput model creation."""

    def test_basic_creation(self):
        inp = GrepInput(pattern="TODO")
        assert inp.pattern == "TODO"
        assert inp.path is None

    def test_all_fields(self):
        inp = GrepInput(
            pattern="error",
            path="/src",
            glob="*.py",
            type="py",
            output_mode="content",
            multiline=True,
            head_limit=50,
            offset=10,
        )
        assert inp.pattern == "error"
        assert inp.output_mode == "content"

    def test_extra_fields(self):
        """Grep allows extra fields like -A, -B, -C, -i, -n."""
        inp = GrepInput.model_validate(
            {"pattern": "test", "-A": 3, "-B": 2, "-i": True}
        )
        assert inp.pattern == "test"
        assert inp.model_extra == {"-A": 3, "-B": 2, "-i": True}


class TestFormatGrepInput:
    """Test HTML input formatting."""

    def test_pattern_only_returns_empty(self):
        """When pattern is the only param, body should be empty."""
        inp = GrepInput(pattern="TODO")
        assert format_grep_input(inp) == ""

    def test_with_path_shows_params_table(self):
        inp = GrepInput(pattern="error", path="/src/app")
        html = format_grep_input(inp)
        assert "tool-params-table" in html
        assert "path" in html
        assert "/src/app" in html
        # Pattern must NOT appear in the body
        assert "error" not in html

    def test_with_multiple_params(self):
        inp = GrepInput(
            pattern="log.*Error",
            path="/src",
            glob="*.ts",
            output_mode="content",
        )
        html = format_grep_input(inp)
        assert "tool-params-table" in html
        assert "path" in html
        assert "glob" in html
        assert "output_mode" in html
        # Pattern excluded from body
        assert "log.*Error" not in html

    def test_extra_fields_shown(self):
        """Extra fields like -A, -B should appear in the params table."""
        inp = GrepInput.model_validate({"pattern": "test", "-A": 5, "-i": True})
        html = format_grep_input(inp)
        assert "tool-params-table" in html
        assert "-A" in html
        assert "-i" in html

    def test_html_escaping(self):
        inp = GrepInput(pattern="<script>", path="/tmp/<dir>")
        html = format_grep_input(inp)
        assert "&lt;dir&gt;" in html
        assert "<dir>" not in html


class TestGrepHtmlTitle:
    """Test HTML title rendering."""

    def test_title_shows_pattern(self):
        inp = GrepInput(pattern="function\\s+\\w+")
        msg = _make_grep_message(inp)
        renderer = HtmlRenderer()
        title = renderer.title_GrepInput(inp, msg)
        assert "🔎" in title
        assert "Grep" in title
        assert "function\\s+\\w+" in title

    def test_title_escapes_html(self):
        inp = GrepInput(pattern="<script>alert(1)</script>")
        msg = _make_grep_message(inp)
        renderer = HtmlRenderer()
        title = renderer.title_GrepInput(inp, msg)
        assert "&lt;script&gt;" in title
        assert "<script>" not in title


class TestGrepMarkdown:
    """Test Markdown renderer has matching Grep support."""

    def test_markdown_title(self):
        inp = GrepInput(pattern="TODO", path="/src")
        msg = _make_grep_message(inp)
        renderer = MarkdownRenderer()
        title = renderer.title_GrepInput(inp, msg)
        assert "Grep" in title
        assert "TODO" in title

    def test_markdown_format_without_glob(self):
        inp = GrepInput(pattern="test")
        msg = _make_grep_message(inp)
        renderer = MarkdownRenderer()
        body = renderer.format_GrepInput(inp, msg)
        assert body == ""

    def test_markdown_format_with_glob(self):
        inp = GrepInput(pattern="test", glob="*.py")
        msg = _make_grep_message(inp)
        renderer = MarkdownRenderer()
        body = renderer.format_GrepInput(inp, msg)
        assert "*.py" in body
