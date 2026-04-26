"""Tests for adjacent SlashCommand ↔ UserSlashCommand pairing (issue #126).

A `Slash Command` (the typed `/cmd`) and the corresponding
`User (slash command)` (expanded prompt or system caveat) represent a
single logical event and must render as a paired unit. They can appear
in either order:

    `/init`  →  Slash Command  then  User (slash command)
    `/exit`  →  User (slash command)  (caveat)  then  Slash Command
"""

from __future__ import annotations

import pytest

from claude_code_log.models import (
    CommandOutputMessage,
    MessageMeta,
    SlashCommandMessage,
    UserSlashCommandMessage,
)
from claude_code_log.renderer import (
    RenderingContext,
    TemplateMessage,
    _identify_message_pairs,
    _try_pair_adjacent,
)


# ----------------------------- helpers ---------------------------------------


def _meta(uuid: str, *, ts: str = "2026-01-01T00:00:00Z") -> MessageMeta:
    return MessageMeta(session_id="s", timestamp=ts, uuid=uuid)


def _slash(ctx: RenderingContext, uuid: str, name: str = "init") -> TemplateMessage:
    msg = TemplateMessage(
        SlashCommandMessage(
            meta=_meta(uuid),
            command_name=name,
            command_args="",
            command_contents="",
        )
    )
    ctx.register(msg)
    return msg


def _user_slash(
    ctx: RenderingContext, uuid: str, text: str = "expanded prompt"
) -> TemplateMessage:
    meta = _meta(uuid)
    meta.is_meta = True
    msg = TemplateMessage(UserSlashCommandMessage(meta=meta, text=text))
    ctx.register(msg)
    return msg


def _cmd_output(
    ctx: RenderingContext, uuid: str, stdout: str = "ok"
) -> TemplateMessage:
    msg = TemplateMessage(
        CommandOutputMessage(meta=_meta(uuid), stdout=stdout, is_markdown=False)
    )
    ctx.register(msg)
    return msg


@pytest.fixture
def ctx() -> RenderingContext:
    return RenderingContext()


# ----------------------------- _try_pair_adjacent ----------------------------


class TestSlashCommandAdjacentPairing:
    """Pair the slash invocation and its expanded prompt — symmetric in order."""

    def test_slash_then_user_slash_pairs(self, ctx: RenderingContext) -> None:
        """`/init` flow: Slash invocation followed by expanded prompt."""
        slash = _slash(ctx, "u1", name="init")
        user_slash = _user_slash(ctx, "u2", text="Please analyze...")

        assert _try_pair_adjacent(slash, user_slash) is True
        assert slash.pair_last == user_slash.message_index
        assert user_slash.pair_first == slash.message_index

    def test_user_slash_then_slash_pairs(self, ctx: RenderingContext) -> None:
        """`/exit` flow: caveat (User slash command) then Slash invocation."""
        caveat = _user_slash(ctx, "v1", text="Caveat: messages below were generated...")
        slash = _slash(ctx, "v2", name="exit")

        assert _try_pair_adjacent(caveat, slash) is True
        assert caveat.pair_last == slash.message_index
        assert slash.pair_first == caveat.message_index

    def test_two_slash_messages_do_not_pair(self, ctx: RenderingContext) -> None:
        """Two adjacent SlashCommand messages are unrelated; no pair."""
        a = _slash(ctx, "a1", name="init")
        b = _slash(ctx, "a2", name="exit")
        assert _try_pair_adjacent(a, b) is False
        assert a.pair_last is None and b.pair_first is None

    def test_two_user_slash_messages_do_not_pair(self, ctx: RenderingContext) -> None:
        a = _user_slash(ctx, "b1", text="one")
        b = _user_slash(ctx, "b2", text="two")
        assert _try_pair_adjacent(a, b) is False


# ----------------------------- regression: existing rules --------------------


class TestExistingPairingRulesPreserved:
    """The new rule must not regress slash-cmd → command-output pairing."""

    def test_slash_then_output_still_pairs(self, ctx: RenderingContext) -> None:
        slash = _slash(ctx, "c1", name="context")
        output = _cmd_output(ctx, "c2", stdout="rendered context")
        assert _try_pair_adjacent(slash, output) is True
        assert slash.pair_last == output.message_index

    def test_user_slash_then_output_still_pairs(self, ctx: RenderingContext) -> None:
        user_slash = _user_slash(ctx, "d1", text="some prompt")
        output = _cmd_output(ctx, "d2", stdout="rendered")
        assert _try_pair_adjacent(user_slash, output) is True
        assert user_slash.pair_last == output.message_index


# ----------------------------- full pass: triples ----------------------------


class TestThreeMessageSequence:
    """The dominant `/cmd` shape in real transcripts is three sibling user
    messages: a UserSlash caveat preamble, the typed SlashCommand, and the
    CommandOutput, all sharing one timestamp. They group as a triple
    (pair_first → pair_middle → pair_last) so the slash-command name stays
    in the rendered title and no message is orphaned."""

    def test_caveat_slash_output_binds_as_triple(self, ctx: RenderingContext) -> None:
        caveat = _user_slash(ctx, "e1", text="Caveat: ...")
        slash = _slash(ctx, "e2", name="exit")
        output = _cmd_output(ctx, "e3", stdout="See ya!")

        _identify_message_pairs([caveat, slash, output])

        # Triple wiring: pair_first owns pair_middle and pair_last;
        # the middle and last members back-reference pair_first.
        assert caveat.pair_middle == slash.message_index
        assert caveat.pair_last == output.message_index
        assert slash.pair_first == caveat.message_index
        assert slash.pair_last == output.message_index
        assert output.pair_first == caveat.message_index

        # Role properties classify each member exclusively.
        assert caveat.is_first_in_pair
        assert not caveat.is_middle_in_pair
        assert not caveat.is_last_in_pair
        assert slash.is_middle_in_pair
        assert not slash.is_first_in_pair
        assert not slash.is_last_in_pair
        assert output.is_last_in_pair
        assert not output.is_first_in_pair
        assert not output.is_middle_in_pair

        # CSS roles emit the right class names for the HTML template.
        assert caveat.pair_role == "pair_first"
        assert slash.pair_role == "pair_middle"
        assert output.pair_role == "pair_last"

    def test_slash_userslash_no_output_pairs_cleanly(
        self, ctx: RenderingContext
    ) -> None:
        """`/init` — no command output — pairs as a 2-msg pair (not triple)."""
        slash = _slash(ctx, "f1", name="init")
        user_slash = _user_slash(ctx, "f2", text="Please analyze...")

        _identify_message_pairs([slash, user_slash])

        assert slash.pair_last == user_slash.message_index
        assert user_slash.pair_first == slash.message_index
        # No middle in a 2-msg pair.
        assert slash.pair_middle is None
        assert user_slash.pair_middle is None
        assert slash.is_first_in_pair
        assert user_slash.is_last_in_pair


# ----------------------------- end-to-end Markdown render --------------------


class TestMarkdownRender:
    """End-to-end Markdown coverage for the dominant `/cmd` triple shape.

    Without the title-borrowing override and `pair_middle` body delegation,
    Markdown would render as `## User (slash command)` with no `/exit`
    visible and the trailing CommandOutput body dropped — both regressions
    surfaced in monk's review of PR #127.
    """

    def _load_triple(
        self, tmp_path, *, ts: str = "2026-04-17T01:13:55Z", cmd: str = "exit"
    ):
        """Write a 3-msg JSONL fixture and load it into TemplateMessages."""
        import json
        from claude_code_log.converter import load_transcript

        lines = [
            {
                "type": "user",
                "uuid": "u1",
                "timestamp": ts,
                "sessionId": "s1",
                "version": "1",
                "parentUuid": None,
                "isSidechain": False,
                "userType": "user",
                "cwd": "/x",
                "isMeta": True,
                "message": {"role": "user", "content": "Caveat: caveat text."},
            },
            {
                "type": "user",
                "uuid": "u2",
                "timestamp": ts,
                "sessionId": "s1",
                "version": "1",
                "parentUuid": "u1",
                "isSidechain": False,
                "userType": "user",
                "cwd": "/x",
                "message": {
                    "role": "user",
                    "content": (
                        f"<command-name>{cmd}</command-name>"
                        f"<command-message>{cmd}</command-message>"
                        f"<command-args></command-args>"
                    ),
                },
            },
            {
                "type": "user",
                "uuid": "u3",
                "timestamp": ts,
                "sessionId": "s1",
                "version": "1",
                "parentUuid": "u2",
                "isSidechain": False,
                "userType": "user",
                "cwd": "/x",
                "message": {
                    "role": "user",
                    "content": "<local-command-stdout>See ya!</local-command-stdout>",
                },
            },
        ]
        fn = tmp_path / "t.jsonl"
        fn.write_text("\n".join(json.dumps(line) for line in lines))
        return load_transcript(fn)

    def test_triple_renders_command_name_and_output(self, tmp_path) -> None:
        from claude_code_log.markdown.renderer import MarkdownRenderer

        msgs = self._load_triple(tmp_path, cmd="exit")
        md = MarkdownRenderer().generate(msgs, "Test")

        # Slash command title borrowed (would otherwise be "User (slash command)").
        assert "🤷 Command `exit`" in md
        assert "User (slash command)" not in md
        # Caveat body kept (delegated from pair_first to itself).
        assert "Caveat: caveat text." in md
        # Output body kept (delegated from pair_first to pair_last — would
        # otherwise be dropped by the orphan empty-title content-skip path).
        assert "See ya!" in md

    def test_triple_renders_with_pair_css_classes_in_html(self, tmp_path) -> None:
        """HTML triple emits pair_first / pair_middle / pair_last in document order."""
        import re
        from claude_code_log.html.renderer import HtmlRenderer

        msgs = self._load_triple(tmp_path, cmd="exit")
        html = HtmlRenderer().generate(msgs, "Test")

        # Extract the message-div opening classes in document order, scoped to
        # the slash-command/command-output blocks.
        classes = [
            m.group(1)
            for m in re.finditer(r"<div class='(message [^']+)'", html)
            if "slash-command" in m.group(1) or "command-output" in m.group(1)
        ]
        assert len(classes) == 3, classes
        assert "pair_first" in classes[0]
        assert "pair_middle" in classes[1]
        assert "pair_last" in classes[2]
        # Triple still surfaces the three distinct messages — slash-command card
        # carries the bare command name in its body code tag, command-output
        # card carries its stdout. Both must remain visible after pairing.
        assert "<code>exit</code>" in html
        assert "See ya!" in html
