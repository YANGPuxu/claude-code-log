"""Regression tests for branch-preview sourcing in ``_build_branch_header``.

The branch ``SessionHeaderMessage.preview`` (which feeds the body
header title, the session/graph index, and the fork-point box's
backlink — all of which must agree on ``Branch • <uuid8> •
<preview>``) is computed once by scanning the branch DAG-line for the
first user entry with non-empty text, via ``extract_text_content``
plus ``create_session_preview``.

These tests pin three cases the single-source rule must handle:

1. **Branch starts with an assistant turn** — the scan must walk past
   the first entry (no user text) and pick up the later user entry's
   preview. This is the case the now-deleted ``_enrich_branch_titles``
   post-pass used to back-fill.
2. **Branch starts with a slash-command user entry** — #129
   precedence: the slash-command body (e.g. ``/exit``) is the first
   user entry with text, so the scan picks it before any later
   user turn ever gets considered. Length is irrelevant — DAG order
   is the precedence.
3. **Branch contains a spawned subagent** — the deleted
   ``_enrich_branch_titles`` had an explicit ``if msg.is_sidechain:
   continue`` guard so an agent's inner first user prompt wouldn't be
   lifted as the *branch's* preview. The new code has no such guard
   because it scans the branch's own DAG-line uuids; agent entries
   live in a separate ``{trunk}#agent-{id}`` DAG-line and are simply
   not in ``branch_uuids``. This test pins that structural invariant:
   if any future DAG-construction change ever leaked an agent uuid
   into a branch line, the preview would silently regress and the
   removed guard would be missed. The test fails loudly if so.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from claude_code_log.converter import load_directory_transcripts
from claude_code_log.models import SessionHeaderMessage
from claude_code_log.renderer import TemplateMessage, generate_template_messages


# ----- fixture builders ----------------------------------------------------


def _user_entry(
    uuid: str,
    parent_uuid: str | None,
    text: str,
    *,
    session_id: str = "s1",
    timestamp: str,
) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": uuid,
        "timestamp": timestamp,
        "parentUuid": parent_uuid,
        "isSidechain": False,
        "userType": "human",
        "cwd": "/tmp",
        "sessionId": session_id,
        "version": "1.0.0",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": text}],
        },
    }


def _assistant_entry(
    uuid: str,
    parent_uuid: str | None,
    text: str,
    *,
    session_id: str = "s1",
    timestamp: str,
    request_id: str,
) -> dict[str, Any]:
    return {
        "type": "assistant",
        "uuid": uuid,
        "timestamp": timestamp,
        "parentUuid": parent_uuid,
        "isSidechain": False,
        "userType": "human",
        "cwd": "/tmp",
        "sessionId": session_id,
        "version": "1.0.0",
        "requestId": request_id,
        "message": {
            "id": uuid,
            "type": "message",
            "role": "assistant",
            "model": "claude-3-sonnet",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        },
    }


def _write_jsonl(path: Path, raw_entries: Iterable[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for entry in raw_entries:
            fh.write(json.dumps(entry) + "\n")


def _branch_headers(roots: list[TemplateMessage]) -> list[SessionHeaderMessage]:
    """Walk the rendered tree and return all branch SessionHeaderMessages
    (any depth — within-fork branch headers hang under their parent)."""

    def walk(messages: list[TemplateMessage]):
        for msg in messages:
            yield msg
            yield from walk(msg.children)

    return [
        msg.content
        for msg in walk(roots)
        if isinstance(msg.content, SessionHeaderMessage) and msg.content.is_branch
    ]


# ----- the tests -----------------------------------------------------------


class TestBranchPreviewFromDagLineScan:
    """Pin that ``_build_branch_header`` computes the branch preview by
    scanning the DAG-line uuids for the first user entry with text."""

    def test_branch_starting_with_assistant_uses_later_user_text(
        self, tmp_path: Path
    ) -> None:
        """A within-session fork whose first entry is an assistant turn
        ("No response requested." after ``/exit`` is the canonical
        production case) must still produce a non-empty preview by
        scanning forward through the branch DAG-line to the next
        user entry with text.

        Before the single-source rewrite, ``_render_messages`` left the
        preview empty for this case and a separate ``_enrich_branch_titles``
        post-pass back-filled it. The new code does the scan up front
        in ``_build_branch_header``.
        """
        # trunk: a (user) → b (assistant) → c (user "Fork point")
        # branch off c, two children — both assistants so the DAG
        # fork-collapse "tool-result side-branch" heuristic
        # (``_stitch_tool_results``, which needs one user and one
        # assistant child) doesn't stitch them and we get a real
        # fork with two branches:
        #   branch 1: d (assistant) → e (user "user text after assistant")
        #   branch 2: f (assistant) — disambiguating sibling
        entries = [
            _user_entry("a", None, "Hello", timestamp="2025-07-01T10:00:00Z"),
            _assistant_entry(
                "b", "a", "Hi", timestamp="2025-07-01T10:01:00Z", request_id="r1"
            ),
            _user_entry("c", "b", "Fork point", timestamp="2025-07-01T10:02:00Z"),
            # branch 1: assistant-first
            _assistant_entry(
                "d",
                "c",
                "Branch 1 leading assistant",
                timestamp="2025-07-01T10:03:00Z",
                request_id="r2",
            ),
            _user_entry(
                "e",
                "d",
                "user text after assistant",
                timestamp="2025-07-01T10:04:00Z",
            ),
            # branch 2: another assistant sibling (see comment above
            # — both children must be the same role to bypass the
            # tool-result stitch and produce a genuine fork)
            _assistant_entry(
                "f",
                "c",
                "alt branch first reply",
                timestamp="2025-07-01T10:05:00Z",
                request_id="r3",
            ),
        ]
        project_dir = tmp_path / "asst-start-project"
        project_dir.mkdir()
        _write_jsonl(project_dir / "s1.jsonl", entries)

        result, session_tree = load_directory_transcripts(project_dir, silent=True)
        roots, _nav, _ctx = generate_template_messages(
            result, session_tree=session_tree
        )

        branch_contents = _branch_headers(roots)
        # Find the branch whose first uuid is 'd' (the assistant-start one).
        asst_first = [b for b in branch_contents if b.first_uuid == "d"]
        assert asst_first, (
            "expected a branch header rooted at uuid 'd' (the assistant-start "
            f"branch); got branches: {[(b.first_uuid, b.preview) for b in branch_contents]}"
        )
        b = asst_first[0]
        assert b.preview == "user text after assistant", (
            "assistant-start branch must scan the DAG-line to the next user "
            f"entry with text; got preview={b.preview!r}"
        )
        # And the assembled title carries the same preview.
        assert "user text after assistant" in (b.title or "")

    def test_branch_starting_with_slash_command_preserves_129_precedence(
        self, tmp_path: Path
    ) -> None:
        """A within-session fork whose first entry is a user-typed
        slash command (e.g. ``/exit``, surfaced as the cleaned 5-char
        form by ``create_session_preview`` → ``simplify_command_tags``)
        must have THAT as its preview — not any later, longer-but-less-
        informative user turn. The DAG-order scan picks the first user
        entry with text and breaks; this structurally preserves the
        #129 precedence rule (see ``test_utils.py::test_create_session_
        preview_strips_slash_command_xml``).
        """
        slash_body = (
            "<command-name>/exit</command-name>"
            "<command-message>exit</command-message>"
            "<command-args></command-args>"
        )
        # trunk: a → b → c (fork point)
        # branch 1: d = /exit (user) → e (assistant "ack") → g (user "Much later, longer, less informative user reply")
        # branch 2: f (user)         — sibling so c is a real junction
        entries = [
            _user_entry("a", None, "Hello", timestamp="2025-07-01T10:00:00Z"),
            _assistant_entry(
                "b", "a", "Hi", timestamp="2025-07-01T10:01:00Z", request_id="r1"
            ),
            _user_entry("c", "b", "Fork point", timestamp="2025-07-01T10:02:00Z"),
            # branch 1: slash-command first
            _user_entry("d", "c", slash_body, timestamp="2025-07-01T10:03:00Z"),
            _assistant_entry(
                "e", "d", "ack", timestamp="2025-07-01T10:04:00Z", request_id="r2"
            ),
            _user_entry(
                "g",
                "e",
                "Much later, longer, less informative user reply that we must not pick",
                timestamp="2025-07-01T10:05:00Z",
            ),
            # branch 2 sibling
            _user_entry("f", "c", "second branch", timestamp="2025-07-01T10:06:00Z"),
        ]
        project_dir = tmp_path / "slash-first-project"
        project_dir.mkdir()
        _write_jsonl(project_dir / "s1.jsonl", entries)

        result, session_tree = load_directory_transcripts(project_dir, silent=True)
        roots, _nav, _ctx = generate_template_messages(
            result, session_tree=session_tree
        )

        branch_contents = _branch_headers(roots)
        slash_first = [b for b in branch_contents if b.first_uuid == "d"]
        assert slash_first, (
            "expected a branch header rooted at uuid 'd' (the slash-command-start "
            f"branch); got branches: {[(b.first_uuid, b.preview) for b in branch_contents]}"
        )
        b = slash_first[0]
        assert b.preview == "/exit", (
            "slash-command branch root must surface as the cleaned '/exit' "
            f"form (#129); got preview={b.preview!r}. A length-based pick "
            "would wrongly choose the later longer reply."
        )
        # Title carries the cleaned form, NOT the raw <command-name> XML.
        assert "/exit" in (b.title or "")
        assert "<command-name>" not in (b.title or "")
        assert "Much later" not in (b.title or "")


class TestBranchPreviewIgnoresAgentInnerPrompts:
    """Pin the structural invariant that ``_build_branch_header``'s
    DAG-line scan can NEVER lift a spawned subagent's inner first user
    prompt as the *branch's* preview.

    The deleted ``_enrich_branch_titles`` post-pass had an explicit
    ``if msg.is_sidechain: continue`` guard for this — needed because
    it iterated ``ctx.messages``, where an agent's wrapped messages
    can carry the branch's ``render_session_id`` (see
    ``_render_messages`` agent-parent handling). The new code has no
    such guard because it walks ``branch_uuids`` (the branch's own
    DAG-line), and agent entries live in a separate
    ``{trunk}#agent-{id}`` DAG-line — re-parented by
    ``_integrate_agent_entries`` before ``build_dag``.

    These tests pin that invariant at two levels: a unit-level check
    against ``_build_branch_header`` directly with crafted inputs
    (the most direct expression of "scan is bounded to
    ``branch_uuids``"), and a property check on
    ``_extract_session_hierarchy`` confirming an agent's synthetic
    sessionId is a *separate* DAG-line whose uuids do not bleed into
    the branch's uuids.
    """

    def test_build_branch_header_scan_is_bounded_to_branch_uuids(self) -> None:
        """Unit-level: even when ``uuid_to_entry`` contains a tempting
        agent user entry, the scan ignores it because that uuid isn't
        in ``branch_uuids``. This is the sharpest expression of the
        invariant — a future change that widens the scan beyond
        ``branch_uuids`` would fail this test immediately.
        """
        from claude_code_log.factories import create_transcript_entry
        from claude_code_log.renderer import (
            RenderingContext,
            _build_branch_header,
        )

        # Branch's own DAG-line: an assistant-start branch (d → e),
        # where e carries the legitimate branch-local user text. The
        # agent's inner first user prompt (agent_first) is in
        # `uuid_to_entry` but NOT in branch_uuids — it lives in a
        # separate DAG-line, just as `_integrate_agent_entries` ensures.
        d_entry = create_transcript_entry(
            {
                "type": "assistant",
                "uuid": "d",
                "timestamp": "2025-07-01T10:03:00Z",
                "parentUuid": "c",
                "isSidechain": False,
                "userType": "human",
                "cwd": "/tmp",
                "sessionId": "s1",
                "version": "1.0.0",
                "requestId": "r2",
                "message": {
                    "id": "d",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-3",
                    "content": [{"type": "text", "text": "Branch 1 leading asst"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            }
        )
        e_entry = create_transcript_entry(
            {
                "type": "user",
                "uuid": "e",
                "timestamp": "2025-07-01T10:04:00Z",
                "parentUuid": "d",
                "isSidechain": False,
                "userType": "human",
                "cwd": "/tmp",
                "sessionId": "s1",
                "version": "1.0.0",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "BRANCH-LOCAL user turn"}],
                },
            }
        )
        agent_first_entry = create_transcript_entry(
            {
                "type": "user",
                "uuid": "agent_first",
                "timestamp": "2025-07-01T10:03:30Z",
                "parentUuid": "d",
                "isSidechain": True,
                "userType": "external",
                "cwd": "/tmp",
                "sessionId": "s1#agent-ag1",
                "version": "1.0.0",
                "agentId": "ag1",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "AGENT INNER PROMPT — MUST NOT be lifted",
                        }
                    ],
                },
            }
        )

        # Branch hierarchy entry. Crucially, ``uuids`` lists ONLY the
        # branch's own DAG-line — d, e — not the agent's inner uuid.
        session_hierarchy = {
            "s1@d_branch": {
                "parent_session_id": "s1",
                "attachment_uuid": "c",
                "depth": 1,
                "is_branch": True,
                "original_session_id": "s1",
                "first_uuid": "d",
                "uuids": ["d", "e"],
            },
        }
        # `uuid_to_entry` knows about ALL entries the renderer iterates
        # over, including the agent's. The scan must IGNORE it.
        uuid_to_entry = {
            "d": d_entry,
            "e": e_entry,
            "agent_first": agent_first_entry,
        }

        result = _build_branch_header(
            branch_sid="s1@d_branch",
            message=d_entry,  # the trigger (branch's first DAG entry)
            session_hierarchy=session_hierarchy,
            session_summaries={},
            session_team_names={},
            uuid_to_entry=uuid_to_entry,
            ctx=RenderingContext(),
        )

        assert result.preview == "BRANCH-LOCAL user turn", (
            "branch preview must come from the branch-local user turn "
            f"(uuid e), NOT the spawned agent's inner prompt; "
            f"got preview={result.preview!r}"
        )
        # Belt-and-braces: the agent prompt text must not appear
        # anywhere in the title either.
        assert "AGENT INNER PROMPT" not in (result.title or "")

    def test_extract_session_hierarchy_keeps_agent_uuids_in_separate_dag_line(
        self, tmp_path: Path
    ) -> None:
        """Property check on the integration: an agent's synthetic
        ``{trunk}#agent-{id}`` session gets its OWN DAG-line whose
        uuids do not appear in any other session's ``uuids``. This is
        the upstream guarantee that ``_build_branch_header``'s scan
        relies on; pinning it here means a regression in
        ``_integrate_agent_entries`` (or its consumers) trips this
        test before it can silently leak agent uuids into a branch's
        preview.
        """

        # Trunk with a Task tool_use spawn anchor. Two assistants off
        # `c` so the fork-collapse heuristic doesn't absorb branch 2
        # (see ``test_branch_starting_with_assistant_uses_later_user_text``
        # for the same workaround and rationale).
        task_tool_use_id = "toolu_spawn"
        agent_id = "ag1"
        trunk_entries: list[dict[str, Any]] = [
            _user_entry("a", None, "Hello", timestamp="2025-07-01T10:00:00Z"),
            _assistant_entry(
                "b", "a", "Hi", timestamp="2025-07-01T10:01:00Z", request_id="r1"
            ),
            _user_entry("c", "b", "Fork", timestamp="2025-07-01T10:02:00Z"),
            # branch 1: assistant-first with a Task tool_use anchoring
            # an agent spawn
            {
                "type": "assistant",
                "uuid": "d",
                "timestamp": "2025-07-01T10:03:00Z",
                "parentUuid": "c",
                "isSidechain": False,
                "userType": "human",
                "cwd": "/tmp",
                "sessionId": "s1",
                "version": "1.0.0",
                "requestId": "r2",
                "message": {
                    "id": "d",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-3",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": task_tool_use_id,
                            "name": "Task",
                            "input": {
                                "subagent_type": "general-purpose",
                                "prompt": "do the thing",
                            },
                        }
                    ],
                    "stop_reason": "tool_use",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            },
            # Tool result anchor for the agent (carries agentId so
            # _integrate_agent_entries can re-parent the sidechain
            # entries to this UUID).
            {
                "type": "user",
                "uuid": "d_result",
                "timestamp": "2025-07-01T10:03:30Z",
                "parentUuid": "d",
                "isSidechain": False,
                "userType": "human",
                "cwd": "/tmp",
                "sessionId": "s1",
                "version": "1.0.0",
                "agentId": agent_id,
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": task_tool_use_id,
                            "content": "done",
                        }
                    ],
                },
            },
            _user_entry(
                "e",
                "d_result",
                "BRANCH-LOCAL user turn",
                timestamp="2025-07-01T10:04:00Z",
            ),
            # branch 2 sibling assistant (bypasses fork-collapse)
            _assistant_entry(
                "f",
                "c",
                "alt branch first reply",
                timestamp="2025-07-01T10:05:00Z",
                request_id="r3",
            ),
        ]

        # Agent sidechain entries — separate file, joined by the
        # standard ``agent-<id>.jsonl`` legacy layout.
        agent_entries: list[dict[str, Any]] = [
            {
                "type": "user",
                "uuid": "agent_first",
                "timestamp": "2025-07-01T10:03:35Z",
                "parentUuid": "d_result",
                "isSidechain": True,
                "userType": "external",
                "cwd": "/tmp",
                "sessionId": "s1",
                "version": "1.0.0",
                "agentId": agent_id,
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "AGENT INNER PROMPT must not be lifted",
                        }
                    ],
                },
            },
            {
                "type": "assistant",
                "uuid": "agent_reply",
                "timestamp": "2025-07-01T10:03:40Z",
                "parentUuid": "agent_first",
                "isSidechain": True,
                "userType": "external",
                "cwd": "/tmp",
                "sessionId": "s1",
                "version": "1.0.0",
                "agentId": agent_id,
                "requestId": "ra",
                "message": {
                    "id": "agent_reply",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-3",
                    "content": [{"type": "text", "text": "agent ack"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            },
        ]

        project_dir = tmp_path / "agent-in-branch-project"
        project_dir.mkdir()
        _write_jsonl(project_dir / "s1.jsonl", trunk_entries)
        _write_jsonl(project_dir / f"agent-{agent_id}.jsonl", agent_entries)

        result, session_tree = load_directory_transcripts(project_dir, silent=True)
        # Property 1: the agent gets its own DAG-line whose session
        # id is synthetic (`{trunk}#agent-{id}`).
        assert session_tree is not None
        agent_sid_candidates = [
            sid for sid in session_tree.sessions if "#agent-" in sid
        ]
        assert agent_sid_candidates, (
            "expected a synthetic `{trunk}#agent-{id}` DAG-line; "
            f"got sessions: {list(session_tree.sessions)}"
        )

        # Property 2: that agent DAG-line's uuids do NOT bleed into
        # any other session's `uuids`. If they ever did, the branch
        # scan could lift the agent's prompt — the very thing the
        # deleted is_sidechain guard used to prevent.
        agent_uuids: set[str] = set()
        for sid in agent_sid_candidates:
            agent_uuids.update(session_tree.sessions[sid].uuids)
        for sid, dl in session_tree.sessions.items():
            if "#agent-" in sid:
                continue
            overlap = agent_uuids & set(dl.uuids)
            assert not overlap, (
                f"agent uuids leaked into non-agent DAG-line {sid!r}: "
                f"{overlap}. This would let _build_branch_header's "
                "DAG-line scan lift an agent's inner prompt as the "
                "branch preview — the regression the deleted "
                "is_sidechain guard used to prevent."
            )

        # Property 3 (end-to-end belt-and-braces): the rendered branch
        # carrying the agent spawn picks the BRANCH-LOCAL preview,
        # not the agent's inner text. This is what would visibly
        # regress if properties 1+2 broke.
        roots, _nav, _ctx = generate_template_messages(
            result, session_tree=session_tree
        )
        branch_contents = _branch_headers(roots)
        branch_d = [b for b in branch_contents if b.first_uuid == "d"]
        assert branch_d, (
            f"expected branch header rooted at 'd'; got: "
            f"{[(b.first_uuid, b.preview) for b in branch_contents]}"
        )
        preview = branch_d[0].preview or ""
        assert "BRANCH-LOCAL" in preview, (
            f"branch preview must come from the branch-local user turn; "
            f"got preview={preview!r}"
        )
        assert "AGENT INNER PROMPT" not in preview, (
            f"branch preview leaked the agent's inner first user prompt; "
            f"got preview={preview!r}"
        )
