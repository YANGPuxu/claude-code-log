"""Parser tests for the teammates feature (issue #91, PR #117)."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_code_log.converter import (
    load_directory_transcripts,
    load_transcript,
)
from claude_code_log.factories.agent_metadata_factory import (
    parse_agent_result_metadata,
)
from claude_code_log.factories.teammate_factory import (
    create_teammate_message,
    find_team_lead_body,
    has_teammate_message,
    iter_teammate_blocks,
)
from claude_code_log.factories.tool_factory import (
    TOOL_INPUT_MODELS,
    TOOL_OUTPUT_PARSERS,
    create_tool_input,
    parse_sendmessage_output,
    parse_taskcreate_output,
    parse_tasklist_output,
    parse_taskupdate_output,
    parse_teamcreate_output,
    parse_teamdelete_output,
)
from claude_code_log.models import (
    AgentResultMetadata,
    AssistantTranscriptEntry,
    MessageMeta,
    SendMessageInput,
    SendMessageOutput,
    TaskCreateInput,
    TaskCreateOutput,
    TaskListInput,
    TaskListOutput,
    TaskUpdateInput,
    TaskUpdateOutput,
    TeamCreateInput,
    TeamCreateOutput,
    TeamDeleteInput,
    TeamDeleteOutput,
    ToolResultContent,
    ToolUseContent,
    UserTranscriptEntry,
)


class TestAgentResultMetadata:
    def test_returns_none_for_plain_text(self) -> None:
        body, meta = parse_agent_result_metadata("Hello, world.")
        assert body == "Hello, world."
        assert meta is None

    def test_returns_none_for_empty(self) -> None:
        body, meta = parse_agent_result_metadata("")
        assert body == ""
        assert meta is None

    def test_parses_agent_id_only(self) -> None:
        text = "Done.\n\nagentId: abc123\n"
        body, meta = parse_agent_result_metadata(text)
        assert body == "Done."
        assert meta is not None
        assert meta.agent_id == "abc123"
        assert meta.worktree_path is None
        assert meta.total_tokens is None

    def test_parses_agent_id_with_trailing_sendmessage_hint(self) -> None:
        text = (
            "Work complete.\n"
            "agentId: a4ca7529 (use SendMessage with to: 'x' to continue this agent)\n"
        )
        body, meta = parse_agent_result_metadata(text)
        assert body == "Work complete."
        assert meta is not None
        # Hint in parens must not be absorbed into the id
        assert meta.agent_id == "a4ca7529"

    def test_parses_worktree_fields(self) -> None:
        text = (
            "Body text.\n"
            "agentId: xyz\n"
            "worktreePath: /home/user/worktrees/agent-xyz\n"
            "worktreeBranch: worktree-agent-xyz\n"
        )
        body, meta = parse_agent_result_metadata(text)
        assert body == "Body text."
        assert meta is not None
        assert meta.agent_id == "xyz"
        assert meta.worktree_path == "/home/user/worktrees/agent-xyz"
        assert meta.worktree_branch == "worktree-agent-xyz"

    def test_parses_usage_block(self) -> None:
        text = (
            "agent response\n"
            "agentId: a\n"
            "worktreePath: /tmp/a\n"
            "worktreeBranch: b-a\n"
            "<usage>total_tokens: 48421\n"
            "tool_uses: 24\n"
            "duration_ms: 802753</usage>"
        )
        body, meta = parse_agent_result_metadata(text)
        assert body == "agent response"
        assert meta is not None
        assert meta.total_tokens == 48421
        assert meta.tool_uses == 24
        assert meta.duration_ms == 802753

    def test_usage_block_only(self) -> None:
        """Pre-teammates transcripts may have <usage> alone."""
        text = (
            "Answer.\n<usage>total_tokens: 10\ntool_uses: 1\nduration_ms: 200</usage>"
        )
        body, meta = parse_agent_result_metadata(text)
        assert body == "Answer."
        assert meta is not None
        assert meta.agent_id is None
        assert meta.total_tokens == 10
        assert meta.tool_uses == 1
        assert meta.duration_ms == 200

    def test_metadata_tail_is_stripped_idempotently(self) -> None:
        text = "Body\n\n\nagentId: x\nworktreePath: /p\n"
        body, meta = parse_agent_result_metadata(text)
        assert body == "Body"
        # Feeding the stripped body back yields None (no tail left).
        _, second = parse_agent_result_metadata(body)
        assert second is None

    def test_result_object_type(self) -> None:
        _, meta = parse_agent_result_metadata("agentId: abc\n")
        assert isinstance(meta, AgentResultMetadata)

    def test_body_mentioning_agent_id_is_preserved(self) -> None:
        """Regression (coderabbit #117): an agent response that itself
        shows an `agentId:` line verbatim (e.g. quoting another agent's
        metadata back) must not be truncated — only the *last* `agentId:`
        line is treated as the metadata anchor.
        """
        # Body contains a literal line-starting ``agentId:`` that would
        # have matched the old first-match anchor; real metadata follows.
        text = (
            "Here's the metadata from the upstream spawn I investigated:\n"
            "\n"
            "agentId: bogus1234\n"
            "worktreePath: /tmp/upstream\n"
            "\n"
            "That's what I found. My own report follows.\n"
            "\n"
            "agentId: real5678\n"
            "worktreePath: /tmp/mine\n"
            "worktreeBranch: wt-real\n"
        )
        body, meta = parse_agent_result_metadata(text)
        # Old code would have truncated at the first ``agentId:`` line,
        # dropping everything after it (including the "real report" line
        # AND the actual metadata).
        assert "agentId: bogus1234" in body
        assert "That's what I found" in body
        assert meta is not None
        assert meta.agent_id == "real5678"
        assert meta.worktree_path == "/tmp/mine"
        assert meta.worktree_branch == "wt-real"

    def test_worktree_path_with_spaces(self) -> None:
        """Regression (coderabbit #117): `worktreePath` with spaces must
        be captured in full, not truncated at the first space."""
        text = (
            "body\n"
            "agentId: abc\n"
            "worktreePath: /home/user/My Worktrees/agent-abc\n"
            "worktreeBranch: feature/agent abc\n"
        )
        body, meta = parse_agent_result_metadata(text)
        assert body == "body"
        assert meta is not None
        assert meta.worktree_path == "/home/user/My Worktrees/agent-abc"
        assert meta.worktree_branch == "feature/agent abc"


def _meta() -> MessageMeta:
    return MessageMeta(session_id="s", timestamp="t", uuid="u")


SINGLE_BLOCK = (
    '<teammate-message teammate_id="alice" color="blue" '
    'summary="relay tests complete">\n'
    "Relay coverage is now 96%.\n"
    "</teammate-message>"
)

MULTI_BLOCK = (
    '<teammate-message teammate_id="alice" color="blue">\n'
    "alice heartbeat: still here.\n"
    "</teammate-message>\n\n"
    '<teammate-message teammate_id="bob" color="green" summary="done">\n'
    "All server tests pass.\n"
    "</teammate-message>\n\n"
    '<teammate-message teammate_id="system">\n'
    "teammate_terminated: alice exited cleanly\n"
    "</teammate-message>"
)


class TestTeammateMessageParser:
    def test_has_teammate_message_detects(self) -> None:
        assert has_teammate_message(SINGLE_BLOCK) is True
        assert has_teammate_message("no tags here") is False
        assert has_teammate_message("<teammate-message") is False  # no close tag

    def test_iter_returns_blocks_in_order(self) -> None:
        ids = [b.teammate_id for b in iter_teammate_blocks(MULTI_BLOCK)]
        assert ids == ["alice", "bob", "system"]

    def test_single_block_attributes_and_body(self) -> None:
        blocks = list(iter_teammate_blocks(SINGLE_BLOCK))
        assert len(blocks) == 1
        b = blocks[0]
        assert b.teammate_id == "alice"
        assert b.color == "blue"
        assert b.summary == "relay tests complete"
        assert b.body == "Relay coverage is now 96%."
        assert b.is_system is False

    def test_block_without_summary(self) -> None:
        text = (
            '<teammate-message teammate_id="alice" color="blue">\n'
            "plain body\n"
            "</teammate-message>"
        )
        b = next(iter(iter_teammate_blocks(text)))
        assert b.summary is None
        assert b.color == "blue"

    def test_system_block_flagged(self) -> None:
        blocks = list(iter_teammate_blocks(MULTI_BLOCK))
        system_block = blocks[-1]
        assert system_block.is_system is True
        assert "teammate_terminated" in system_block.body

    def test_create_returns_none_without_block(self) -> None:
        assert create_teammate_message(_meta(), "just some text") is None

    def test_create_batch_single_block(self) -> None:
        content = create_teammate_message(_meta(), SINGLE_BLOCK)
        assert content is not None
        assert len(content.blocks) == 1
        assert content.blocks[0].teammate_id == "alice"
        assert content.leading_text is None
        assert content.trailing_text is None
        assert content.message_type == "teammate"
        assert content.has_markdown is True

    def test_create_batch_mixed_teammates(self) -> None:
        content = create_teammate_message(_meta(), MULTI_BLOCK)
        assert content is not None
        assert [b.teammate_id for b in content.blocks] == ["alice", "bob", "system"]

    def test_leading_and_trailing_text_preserved(self) -> None:
        text = f"Before text\n\n{SINGLE_BLOCK}\n\nAfter text"
        content = create_teammate_message(_meta(), text)
        assert content is not None
        assert content.leading_text == "Before text"
        assert content.trailing_text == "After text"

    def test_find_team_lead_body(self) -> None:
        wrapped = (
            '<teammate-message teammate_id="team-lead" color="cyan">\n'
            "do the thing\n"
            "</teammate-message>"
        )
        assert find_team_lead_body(wrapped) == "do the thing"
        assert find_team_lead_body(SINGLE_BLOCK) is None
        assert find_team_lead_body("") is None


def _tr_text(text: str) -> ToolResultContent:
    """Build a ToolResultContent with a single text block body."""
    return ToolResultContent(
        type="tool_result",
        tool_use_id="tu_fake",
        content=[{"type": "text", "text": text}],
    )


class TestTeammateToolInputs:
    """All six teammate tool names route to a typed BaseModel input."""

    def test_inputs_registered(self) -> None:
        for name, cls in {
            "TeamCreate": TeamCreateInput,
            "TeamDelete": TeamDeleteInput,
            "TaskCreate": TaskCreateInput,
            "TaskUpdate": TaskUpdateInput,
            "TaskList": TaskListInput,
            "SendMessage": SendMessageInput,
        }.items():
            assert TOOL_INPUT_MODELS.get(name) is cls, f"{name} not mapped"

    def test_teamcreate_input(self) -> None:
        parsed = create_tool_input(
            "TeamCreate",
            {
                "team_name": "x",
                "description": "d",
                "agent_type": "team-lead",
            },
        )
        assert isinstance(parsed, TeamCreateInput)
        assert parsed.team_name == "x"
        assert parsed.agent_type == "team-lead"

    def test_taskupdate_input_partial(self) -> None:
        parsed = create_tool_input("TaskUpdate", {"taskId": "1", "status": "completed"})
        assert isinstance(parsed, TaskUpdateInput)
        assert parsed.taskId == "1"
        assert parsed.status == "completed"
        assert parsed.owner is None

    def test_tasklist_input_empty(self) -> None:
        parsed = create_tool_input("TaskList", {})
        assert isinstance(parsed, TaskListInput)

    def test_sendmessage_input(self) -> None:
        parsed = create_tool_input(
            "SendMessage",
            {
                "type": "shutdown_request",
                "recipient": "alice",
                "content": "go home",
            },
        )
        assert isinstance(parsed, SendMessageInput)
        assert parsed.recipient == "alice"
        assert parsed.content == "go home"


class TestTeammateToolOutputs:
    """JSON/plain-text tool results parse into typed outputs."""

    def test_output_parsers_registered(self) -> None:
        for name in (
            "TeamCreate",
            "TeamDelete",
            "TaskCreate",
            "TaskUpdate",
            "TaskList",
            "SendMessage",
        ):
            assert name in TOOL_OUTPUT_PARSERS, f"{name} parser missing"

    def test_teamcreate_output(self) -> None:
        payload = (
            '{"team_name":"test-coverage",'
            '"team_file_path":"/teams/test-coverage/config.json",'
            '"lead_agent_id":"team-lead@test-coverage"}'
        )
        out = parse_teamcreate_output(_tr_text(payload), None)
        assert isinstance(out, TeamCreateOutput)
        assert out.team_name == "test-coverage"
        assert out.lead_agent_id == "team-lead@test-coverage"

    def test_teamcreate_output_rejects_non_json(self) -> None:
        out = parse_teamcreate_output(_tr_text("not-json"), None)
        assert out is None

    def test_teamdelete_extracts_active_members(self) -> None:
        payload = (
            '{"success":false,'
            '"message":"Cannot cleanup team with 2 active member(s): alice, bob. Try shutdown first.",'
            '"team_name":"test-coverage"}'
        )
        out = parse_teamdelete_output(_tr_text(payload), None)
        assert isinstance(out, TeamDeleteOutput)
        assert out.success is False
        assert out.active_members == ["alice", "bob"]
        assert out.team_name == "test-coverage"

    def test_teamdelete_success_no_members(self) -> None:
        payload = '{"success":true,"message":"Team deleted.","team_name":"x"}'
        out = parse_teamdelete_output(_tr_text(payload), None)
        assert isinstance(out, TeamDeleteOutput)
        assert out.success is True
        assert out.active_members is None

    def test_teamdelete_rejects_string_success(self) -> None:
        """Regression (coderabbit #117): stringified `"false"` must not
        coerce into `success=True`."""
        payload = '{"success":"false","message":"no","team_name":"x"}'
        out = parse_teamdelete_output(_tr_text(payload), None)
        assert out is None

    def test_sendmessage_rejects_string_success(self) -> None:
        """Regression (coderabbit #117): same as TeamDelete — the parser
        falls through rather than silently mis-rendering a string bool."""
        payload = (
            '{"success":"true","message":"sent","request_id":"r","target":"alice"}'
        )
        out = parse_sendmessage_output(_tr_text(payload), None)
        assert out is None

    def test_sendmessage_rejects_non_string_message(self) -> None:
        payload = '{"success":true,"message":123,"target":"alice"}'
        out = parse_sendmessage_output(_tr_text(payload), None)
        assert out is None

    def test_teamdelete_active_members_without_trailing_period(self) -> None:
        """Defensive: the active-members regex must not require a period."""
        payload = (
            '{"success":false,'
            '"message":"Cannot cleanup team with 2 active member(s): alice, bob",'
            '"team_name":"x"}'
        )
        out = parse_teamdelete_output(_tr_text(payload), None)
        assert isinstance(out, TeamDeleteOutput)
        assert out.active_members == ["alice", "bob"]

    def test_taskcreate_output(self) -> None:
        out = parse_taskcreate_output(
            _tr_text("Task #3 created successfully: Add relay tests"),
            None,
        )
        assert isinstance(out, TaskCreateOutput)
        assert out.task_id == "3"
        assert out.subject == "Add relay tests"

    def test_taskcreate_rejects_unrecognized(self) -> None:
        out = parse_taskcreate_output(_tr_text("Completely different"), None)
        assert out is None

    def test_taskupdate_output(self) -> None:
        out = parse_taskupdate_output(_tr_text("Updated task #1 owner, status"), None)
        assert isinstance(out, TaskUpdateOutput)
        assert out.success is True
        assert out.task_id == "1"
        assert out.updated_fields == {"owner": True, "status": True}

    def test_taskupdate_strips_trailing_punctuation(self) -> None:
        """Regression (coderabbit #117): a trailing period or semicolon
        on the field list must not leak into the last field key."""
        out = parse_taskupdate_output(_tr_text("Updated task #1 owner, status."), None)
        assert isinstance(out, TaskUpdateOutput)
        assert out.updated_fields == {"owner": True, "status": True}

        out = parse_taskupdate_output(_tr_text("Updated task #2 subject, owner;"), None)
        assert isinstance(out, TaskUpdateOutput)
        assert out.updated_fields == {"subject": True, "owner": True}

    def test_tasklist_output(self) -> None:
        text = (
            "#1 [completed] Add relay tests (alice)\n"
            "#2 [in_progress] Add server tests (bob)\n"
            "#3 [pending] Merge branches"
        )
        out = parse_tasklist_output(_tr_text(text), None)
        assert isinstance(out, TaskListOutput)
        assert len(out.tasks) == 3
        assert out.tasks[0].status == "completed"
        assert out.tasks[0].owner == "alice"
        assert out.tasks[2].owner is None

    def test_tasklist_returns_none_on_unknown_format(self) -> None:
        out = parse_tasklist_output(_tr_text("This is not a task list."), None)
        assert out is None

    def test_sendmessage_output(self) -> None:
        payload = (
            '{"success":true,'
            '"message":"Shutdown request sent to alice.",'
            '"request_id":"shutdown-1@alice",'
            '"target":"alice"}'
        )
        out = parse_sendmessage_output(_tr_text(payload), None)
        assert isinstance(out, SendMessageOutput)
        assert out.success is True
        assert out.target == "alice"
        assert out.request_id == "shutdown-1@alice"


# ---------------------------------------------------------------------------
# End-to-end fixture integration
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "test_data" / "teammates"
MAIN_SESSION = "ef000000-0000-4000-8000-000000000001"
MAIN_JSONL = FIXTURE_DIR / f"{MAIN_SESSION}.jsonl"
ALICE_AGENT_ID = "aaaa111111111111"
BOB_AGENT_ID = "bbbb222222222222"


@pytest.fixture(scope="module")
def fixture_messages() -> list:
    return load_transcript(MAIN_JSONL, cache_manager=None, silent=True)


@pytest.fixture(scope="module")
def fixture_dag() -> tuple[list, object]:
    return load_directory_transcripts(FIXTURE_DIR, cache_manager=None, silent=True)


class TestTeammatesFixtureLoading:
    def test_main_and_both_subagents_load(self, fixture_messages: list) -> None:
        # 22 main + 3 alice + 3 bob = 28 entries
        assert len(fixture_messages) == 28

    def test_alice_subagent_linked_via_primary_path(
        self, fixture_messages: list
    ) -> None:
        alice_entries = [
            m for m in fixture_messages if getattr(m, "agentId", None) == ALICE_AGENT_ID
        ]
        assert len(alice_entries) >= 4  # tool_result + 3 subagent entries

    def test_bob_subagent_linked_via_prompt_hash(self, fixture_messages: list) -> None:
        bob_entries = [
            m for m in fixture_messages if getattr(m, "agentId", None) == BOB_AGENT_ID
        ]
        # Without the fallback bob wouldn't be linked at all (0 entries).
        assert len(bob_entries) >= 4

    def test_bob_tool_result_back_patched_with_agentid(
        self, fixture_messages: list
    ) -> None:
        # Find the tool_result for bob's Task and confirm the prompt-hash
        # fallback set its agentId.
        tool_use_id: str | None = None
        for m in fixture_messages:
            if isinstance(m, AssistantTranscriptEntry):
                for item in m.message.content:
                    if (
                        isinstance(item, ToolUseContent)
                        and item.name == "Task"
                        and item.input.get("name") == "bob"
                    ):
                        tool_use_id = item.id
                        break
        assert tool_use_id is not None

        for m in fixture_messages:
            if not isinstance(m, UserTranscriptEntry):
                continue
            for c in m.message.content:
                if isinstance(c, ToolResultContent) and c.tool_use_id == tool_use_id:
                    assert m.agentId == BOB_AGENT_ID
                    return
        pytest.fail("bob tool_result not found")


class TestTeammatesIntegrateAgentEntries:
    def test_synthetic_session_ids_per_agent(self, fixture_dag: tuple) -> None:
        messages, _ = fixture_dag
        alice_sessions = {
            m.sessionId
            for m in messages
            if isinstance(m, (AssistantTranscriptEntry, UserTranscriptEntry))
            and getattr(m, "isSidechain", False)
            and getattr(m, "agentId", None) == ALICE_AGENT_ID
        }
        bob_sessions = {
            m.sessionId
            for m in messages
            if isinstance(m, (AssistantTranscriptEntry, UserTranscriptEntry))
            and getattr(m, "isSidechain", False)
            and getattr(m, "agentId", None) == BOB_AGENT_ID
        }
        assert alice_sessions == {f"{MAIN_SESSION}#agent-{ALICE_AGENT_ID}"}
        assert bob_sessions == {f"{MAIN_SESSION}#agent-{BOB_AGENT_ID}"}

    def test_each_agent_root_anchored_to_its_tool_result(
        self, fixture_dag: tuple
    ) -> None:
        messages, _ = fixture_dag

        # Anchor UUID for each agent = the tool_result entry carrying its agentId
        anchors: dict[str, str] = {}
        for m in messages:
            if (
                isinstance(m, UserTranscriptEntry)
                and not m.isSidechain
                and m.agentId in {ALICE_AGENT_ID, BOB_AGENT_ID}
            ):
                anchors[m.agentId] = m.uuid

        assert set(anchors) == {ALICE_AGENT_ID, BOB_AGENT_ID}, anchors

        # Every sidechain root (parentUuid -> anchor uuid) for each agent
        # must point to that agent's anchor.
        for m in messages:
            if not isinstance(m, UserTranscriptEntry):
                continue
            if not m.isSidechain:
                continue
            if not m.agentId:
                continue
            # First message of each sidechain: parentUuid now anchored
            # (we crafted the fixture so the first alice/bob sidechain
            # entry's parentUuid was None).
            if m.uuid.startswith("aaaaaaaa-0000-4000-8000-000000000001") or (
                m.uuid.startswith("bbbbbbbb-0000-4000-8000-000000000001")
            ):
                assert m.parentUuid == anchors[m.agentId]


class TestTeammatesFactoryIntegration:
    def test_alice_task_output_metadata_populated(self, fixture_messages: list) -> None:
        """Parse the fixture via the factory pipeline and confirm the
        alice Task tool_result carries an AgentResultMetadata with the
        values embedded in the markdown tail."""
        from claude_code_log.factories.tool_factory import create_tool_output

        found_alice_metadata = False
        for m in fixture_messages:
            if not isinstance(m, UserTranscriptEntry):
                continue
            for c in m.message.content:
                if (
                    isinstance(c, ToolResultContent)
                    and m.agentId == ALICE_AGENT_ID
                    and c.tool_use_id.startswith("tu_Task_")
                ):
                    parsed = create_tool_output("Task", c)
                    # Typed TaskOutput with structured metadata
                    assert hasattr(parsed, "metadata")
                    meta = getattr(parsed, "metadata")
                    assert meta is not None
                    assert meta.agent_id == ALICE_AGENT_ID
                    assert meta.total_tokens == 12345
                    assert meta.tool_uses == 5
                    assert meta.duration_ms == 60000
                    # Body stripped of the metadata tail
                    result_text = getattr(parsed, "result")
                    assert "Relay tests added" in result_text
                    assert "agentId:" not in result_text
                    found_alice_metadata = True
        assert found_alice_metadata

    def test_identical_prompts_do_not_collide(self, tmp_path: Path) -> None:
        """Regression: two Tasks with identical prompts must link to
        *different* subagent files. Reported by monk in PR #117 review —
        the inner match loop used to overwrite the first patch when a
        second agent file's first-message body normalized to the same
        string.
        """
        import json as _json

        session_id = "dead0000-0000-4000-8000-000000000001"
        main_path = tmp_path / f"{session_id}.jsonl"
        subagents_dir = tmp_path / session_id / "subagents"
        subagents_dir.mkdir(parents=True)

        agent_x = "xxxx1111111111111"
        agent_y = "yyyy2222222222222"

        shared_prompt = "Do the thing."
        base = {
            "isSidechain": False,
            "userType": "external",
            "cwd": "/t",
            "sessionId": session_id,
            "version": "2.1.34",
        }

        def entry(**kw: object) -> dict:
            return {**base, **kw}

        def main_uuid(n: int) -> str:
            return f"00000000-0000-4000-8000-{n:012d}"

        entries = [
            # U1: initial user prompt
            entry(
                parentUuid=None,
                uuid=main_uuid(1),
                timestamp="2026-04-19T10:01:00Z",
                type="user",
                message={"role": "user", "content": [{"type": "text", "text": "go"}]},
            ),
            # A1: Task tool_use → teammate X (identical prompt)
            entry(
                parentUuid=main_uuid(1),
                uuid=main_uuid(2),
                timestamp="2026-04-19T10:02:00Z",
                type="assistant",
                message={
                    "id": "msg_a",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-opus-4-7",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu-A",
                            "name": "Task",
                            "input": {
                                "prompt": shared_prompt,
                                "subagent_type": "general-purpose",
                                "description": "teammate X",
                                "name": "x",
                            },
                        }
                    ],
                    "stop_reason": None,
                },
            ),
            # U2: tool_result for tu-A (no agentId — forces fallback)
            entry(
                parentUuid=main_uuid(2),
                uuid=main_uuid(3),
                timestamp="2026-04-19T10:03:00Z",
                type="user",
                message={
                    "role": "user",
                    "content": [
                        {
                            "tool_use_id": "tu-A",
                            "type": "tool_result",
                            "content": [{"type": "text", "text": "done by x"}],
                        }
                    ],
                },
            ),
            # A2: Task tool_use → teammate Y (identical prompt)
            entry(
                parentUuid=main_uuid(3),
                uuid=main_uuid(4),
                timestamp="2026-04-19T10:04:00Z",
                type="assistant",
                message={
                    "id": "msg_b",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-opus-4-7",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu-B",
                            "name": "Task",
                            "input": {
                                "prompt": shared_prompt,
                                "subagent_type": "general-purpose",
                                "description": "teammate Y",
                                "name": "y",
                            },
                        }
                    ],
                    "stop_reason": None,
                },
            ),
            # U3: tool_result for tu-B (no agentId)
            entry(
                parentUuid=main_uuid(4),
                uuid=main_uuid(5),
                timestamp="2026-04-19T10:05:00Z",
                type="user",
                message={
                    "role": "user",
                    "content": [
                        {
                            "tool_use_id": "tu-B",
                            "type": "tool_result",
                            "content": [{"type": "text", "text": "done by y"}],
                        }
                    ],
                },
            ),
        ]

        with main_path.open("w") as f:
            for e in entries:
                f.write(_json.dumps(e) + "\n")

        # Two agent files whose first message bodies both normalize to the
        # shared prompt.
        for agent_id in (agent_x, agent_y):
            agent_entry = {
                "parentUuid": None,
                "isSidechain": True,
                "agentId": agent_id,
                "userType": "external",
                "cwd": "/t",
                "sessionId": session_id,
                "version": "2.1.34",
                "uuid": f"aa{agent_id[:10]}-0000-4000-8000-000000000001",
                "timestamp": "2026-04-19T10:10:00Z",
                "type": "user",
                "message": {"role": "user", "content": shared_prompt},
            }
            with (subagents_dir / f"agent-{agent_id}.jsonl").open("w") as f:
                f.write(_json.dumps(agent_entry) + "\n")

        messages = load_transcript(main_path, cache_manager=None, silent=True)

        # Collect the agentId each tool_result got patched with.
        patched: dict[str, str] = {}
        for m in messages:
            if not isinstance(m, UserTranscriptEntry):
                continue
            for c in m.message.content:
                if isinstance(c, ToolResultContent) and c.tool_use_id in {
                    "tu-A",
                    "tu-B",
                }:
                    if m.agentId:
                        patched[c.tool_use_id] = m.agentId

        # Both Tasks linked, to *different* agents (no overwrite).
        assert patched.keys() == {"tu-A", "tu-B"}, patched
        assert set(patched.values()) == {agent_x, agent_y}, patched
        assert patched["tu-A"] != patched["tu-B"]

    def test_teammate_message_content_parsed(self, fixture_messages: list) -> None:
        """The user entry carrying multiple <teammate-message> blocks becomes
        a TeammateMessage content model through the user_factory pipeline."""
        from claude_code_log.factories.user_factory import create_user_message
        from claude_code_log.factories.meta_factory import create_meta
        from claude_code_log.models import TeammateMessage

        batched: list[TeammateMessage] = []
        for m in fixture_messages:
            # Restrict to main-session user entries (bob's subagent first
            # message is also a teammate-message wrapper, but that's the
            # subject of the prompt-hash test above).
            if not isinstance(m, UserTranscriptEntry) or m.isSidechain:
                continue
            meta = create_meta(m)
            text_bits: list[str] = []
            for c in m.message.content:
                if hasattr(c, "text"):
                    text_bits.append(getattr(c, "text"))
            text = "\n".join(text_bits)
            content = create_user_message(meta, list(m.message.content), text)
            if isinstance(content, TeammateMessage):
                batched.append(content)

        # Expect two: U14 (single alice block), U15 (alice+bob+system)
        assert len(batched) == 2
        block_counts = sorted(len(b.blocks) for b in batched)
        assert block_counts == [1, 3]
        # System block flagged in the mixed entry
        mixed = next(b for b in batched if len(b.blocks) == 3)
        assert any(blk.is_system for blk in mixed.blocks)
