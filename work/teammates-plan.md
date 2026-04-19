# Teammates Support — Plan (#91)

## Status: planning complete, PR 1 not yet started

This file is the spec anchor for the multi-PR work supporting Claude Code's
teammates feature (research preview, `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`,
CC 2.1.32+). Load this first when resuming.

Parent issue: [#91 Support teammates](https://github.com/daaain/claude-code-log/issues/91).
The body of #91 has extensive real-data snippets (TeamCreate, Task, TaskUpdate,
TaskList, SendMessage, TeamDelete, `<teammate-message>` XML).

Preceding work: [PR #115](https://github.com/daaain/claude-code-log/pull/115)
(merged at `52180cd`) fixed a DAG walker bug that stranded anchor tool_results
for parallel `Task` calls. After #115 all subagent files load, but teammate
relationships are not presented as a first-class concept yet.

---

## Scope summary

"Teammate" is a distinct CC feature, not just parallel `Task` calls.
It adds:

- **Tools**: `TeamCreate`, `TeamDelete`, `TaskCreate`, `TaskUpdate`, `TaskList`,
  `SendMessage`; plus new fields on existing `Task` (`team_name`, `name`, `mode`,
  `run_in_background`, `teammate_id`, `agent_id`, `color`, `tmux_*`, …).
- **Message shape**: User entries whose `message.content` is a string
  containing one or more `<teammate-message teammate_id="…" color="…"
  summary="…">body</teammate-message>` blocks — possibly multiple, possibly
  from different teammates intermingled in one entry.
- **Metadata**: top-level `teamName` on entries during team activity.

## The linking problem (key constraint)

Teammate subagent JSONLs live at
`~/.claude/projects/<project>/<session-id>/subagents/agent-*.jsonl`.
First message has `parentUuid: null` and **no `agentId`** top-level field
back to the spawning `Task` tool_use.

Observation from main (#2197, with concrete UUIDs):

- **Agent tool_use** (e.g. `fc61c13e-920…`): prompt is the tool-use input.
- **Tool result** (e.g. `7b8e89fe-c48…`, parent of the tool_use):
  Markdown body + metadata tail:
  ```
  agentId: a4ca7529859c158c2 (use SendMessage with to: '...' to continue this agent)
  worktreePath: /.../worktrees/agent-a4ca7529
  worktreeBranch: worktree-agent-a4ca7529
  <usage>total_tokens: 48421
  tool_uses: 24
  duration_ms: 802753</usage>
  ```
  **`agentId` is in the Markdown body, not a top-level field.** Parseable.
- **User sidechain** (e.g. `ec72d4f9-49f…`, in the agent JSONL): starts with
  `You are **alice**, a teammate in ...` — **same prompt** as the Agent
  tool_use input.
- **Assistant sidechain** (e.g. `534fc06f-77b…`, parent of the User sidechain):
  the agent's first actual response.

### Linking strategy (per main #2197)

1. **Primary**: parse `agentId: <id>` from tool_result Markdown tail. This
   also solves **#79** (explicit sub-agent linking instead of the timestamp
   fallback that currently happens to work for sync sub-agents) and feeds
   **#90** (async Task agents, same metadata shape).
2. **Fallback**: prompt-hash matching — hash the Task tool_use's prompt
   input, hash the first `<teammate-message teammate_id="team-lead">` body
   in each candidate agent file, match. Use when tool_result metadata is
   absent (older transcripts, teammates where the link isn't emitted).

---

## 3-PR staging

### PR 1 — `dev/teammates-parsing`: parsing + data model (foundational, no UI)

**Models** (`claude_code_log/models.py`):

- New typed `BaseModel`s:
  - `TeamCreateInput` (`team_name`, `description`, `agent_type`)
  - `TeamCreateOutput` (`team_name`, `team_file_path`, `lead_agent_id`)
  - `TeamDeleteInput` (empty)
  - `TeamDeleteOutput` (`success: bool`, `message`, `team_name`, optional active-member info)
  - `TaskCreateInput` (`subject`, `description`, `activeForm`)
  - `TaskCreateOutput` (`task` dict with `id`, `subject`)
  - `TaskUpdateInput` (`taskId`, `owner`, `status`)
  - `TaskUpdateOutput` (`success`, `taskId`, `updatedFields`, `statusChange: {from, to}`)
  - `TaskListInput` (empty)
  - `TaskListOutput` (`tasks: list[{id, subject, status, owner, blockedBy}]`)
  - `SendMessageInput` (`type`, `recipient`, `content`)
  - `SendMessageOutput` (`success`, `message`, `request_id`, `target`)
- Extend existing `TaskInput` with: `team_name`, `name`, `mode`,
  `run_in_background` (Optional). Existing `TaskOutput` already has `teammate_id`
  / `agent_id` / `color` — verify and add the `tmux_*` / `plan_mode_required`
  fields via the spawned-teammate pathway.
- Add to `ToolInput` / `ToolOutput` unions.
- New `MessageContent` subclass: `TeammateMessage` (`teammate_id`, `color`,
  `summary: Optional[str]`, `body: str`, `is_system: bool` for e.g.
  `teammate_terminated` from teammate_id="system"). Message-type: `"teammate"`.
- Optional: `TeammateMessageBatch` if we want to group multiple
  `<teammate-message>` blocks from a single User entry into one card; or render
  each as its own `TeammateMessage` and leave them as siblings. **Decision:
  prefer separate siblings** — simpler, matches existing per-block rendering,
  and intermingled teammates render cleanly.
- Add `teamName: Optional[str]` field on `BaseTranscriptEntry` for carrying
  the entry-level marker through `MessageMeta`.
- Add `team_name: Optional[str]` to `MessageMeta`.

**Factories** (`claude_code_log/factories/`):

- New `teammate_factory.py`:
  - Detect a User entry whose `message.content` is a `str` containing
    `<teammate-message ...>...</teammate-message>` — parse with a small
    hand-written tokenizer (regex `re.DOTALL` on the block, attribute
    extraction — no real XML parser needed, keeps the body intact even if
    it contains XML-looking text).
  - For each block: produce one `TeammateMessage` content item.
  - Mixed-content (some text + some teammate-message blocks) also split into
    pieces sensibly — fall through to generic user text for non-matching
    content.
- New `tool_result_metadata_parser` (in existing `tool_factory.py` or new
  `agent_metadata_factory.py`):
  - Regex out `agentId:`, `worktreePath:`, `worktreeBranch:` lines from the
    Markdown tail of a tool_result's text content.
  - Extract the `<usage>…</usage>` block into structured fields
    (`total_tokens`, `tool_uses`, `duration_ms`).
  - Attach the parsed metadata to the `ToolResultMessage` as a new
    `agent_metadata: Optional[AgentResultMetadata]` field. Strip the metadata
    tail from the displayed body so the rendering stays clean.
- Tool input/output parser additions in `tool_factory.py` for each of the
  six new tools.
- Propagate `teamName` from `BaseTranscriptEntry` into `MessageMeta` in
  `meta_factory.py`.

**Converter** (`claude_code_log/converter.py`):

- In `load_transcript`: after parsing, collect the subagent JSONL files from
  the `<session-id>/subagents/` directory. For each file, read only its first
  entry to extract the `<teammate-message teammate_id="team-lead">` body (or
  the raw first-message text when it's a plain prompt).
- Build a linking map:
  1. For each main-session Task tool_use whose tool_result's parsed
     `agent_metadata.agentId` is set: map that agentId to the subagent file.
  2. For Task tool_uses without a parseable agentId: compute a prompt-hash
     (normalized: strip whitespace, lowercase) of the tool_use's prompt input
     and compare against the prompt-hash of each candidate subagent file's
     first message body. Exact match wins.
- Feed resolved (Task tool_use UUID → subagent entries) into the existing
  agent-integration pathway (`_integrate_agent_entries`) so the subagent
  entries get a synthetic sessionId and anchor to the Task tool_use uuid.
- Teammate synthetic session IDs use `{main}#teammate-{name}@{team}` for
  teammate-spawned agents and retain `#agent-{agentId}` for regular
  sub-agents — easy to distinguish downstream.

**Fixture** (`test/test_data/teammates/`):

- Trim-down one of the real teammate transcripts I found on this system
  (e.g. semindexer projects) — keep only:
  - main .jsonl with: 1 TeamCreate + 2 TaskCreate + 2 Task (spawn) + 1
    TaskUpdate (assignment) + 1 TaskList + 1 SendMessage + 1 TeamDelete +
    2 User entries each containing a `<teammate-message>` + whatever
    structural entries are needed to keep the DAG intact.
  - 2 agent-*.jsonl files (one per teammate) with ~5 entries each.
  - Sanitize paths / uuids to stable synthetic values.
- Keep total under ~300 lines so it stays readable.

**Tests** (`test/test_teammates_parsing.py` — new file):

- Tool input/output model validation (one per tool).
- `<teammate-message>` XML parsing: single block, multiple blocks, blocks
  from different teammates intermingled, block with no `summary` attribute,
  block from `teammate_id="system"` (terminate notification).
- tool_result metadata extraction: agentId-only, agentId + worktree, full
  metadata with `<usage>`, none (clean tool_result body left alone).
- Subagent linking: agentId match, prompt-hash fallback, neither (no link,
  no crash).
- End-to-end: load the fixture, verify synthetic sessionIds, verify
  `_integrate_agent_entries` anchors the right subagent to the right Task.

**Non-goals for PR 1**: no UI changes, no new formatters, no CSS. The
generic renderer keeps rendering teammates' tools as param tables —
ugly but functional. This keeps PR 1 reviewable.

**Estimated size**: ~600 LOC + ~400 LOC tests + 300 LOC fixture = ~1300 LOC
diff, single commit or small series.

### PR 2 — `dev/teammates-rendering`: structured rendering + colors

**HTML formatters** (`claude_code_log/html/tool_formatters.py`):

- `format_teamcreate_input` / `format_teamcreate_output` — render as a team
  card with `team_name` and `lead_agent_id`.
- `format_taskcreate_input` — task-creation card with subject/description.
- `format_taskupdate_input` — "task #N: status (old → new), owner: X"
  with the teammate's color.
- `format_tasklist_output` — a small HTML table of tasks with id/subject/
  status/owner (owner colored per teammate).
- `format_sendmessage_input` — lead → teammate card with colored recipient.
- `format_teamdelete_output` — team-deletion notice (success or "active members
  present" warning).
- Extend `format_task_input` / `format_task_output` to surface teammate-spawn
  fields (color badge, name, run_in_background indicator).

**HTML rendering for TeammateMessage** (`claude_code_log/html/user_formatters.py`
 or new `teammate_formatter.py`):

- `format_teammate_content(content)` — a message bubble with:
  - Colored left border using `--cc-<color>` CSS custom property.
  - Teammate badge: icon + `teammate_id` in the color.
  - Optional summary line in italics.
  - Body rendered as Markdown.
  - Distinct styling for `teammate_id="system"` terminate notifications.

**Color propagation** (`claude_code_log/renderer.py`):

- During `_render_messages` first pass, accumulate a `teammate_id → color`
  map: the first sighting of a `color` for a given `teammate_id` wins
  (authoritative source is the Task tool_result `color` field; later
  `<teammate-message color="…">` is used as fallback if the Task result
  lacks it).
- Pass the map into `RenderingContext`. TeammateMessage formatter and
  TaskUpdate formatter look up color from the map when the entry itself
  lacks it.

**CSS** (`claude_code_log/html/templates/components/message_styles.css`
and a new `teammate_styles.css` component):

- Named color tokens for Claude Code's natural palette:
  `--cc-blue`, `--cc-yellow`, `--cc-green`, `--cc-red`, `--cc-purple`,
  `--cc-orange`, `--cc-cyan`, `--cc-pink`, `--cc-gray`. Pick readable
  values for both light and dark modes.
- `.teammate-message` class: left border using `--cc-<color>`, left
  alignment (**not** right like human user messages), padding, color-tinted
  background.
- `.teammate-badge` class for the identity pill.
- Task-board table styling for `.task-list` renderer output.

**Markdown renderers** (`claude_code_log/markdown/renderer.py`):

- Mirror each new tool formatter. Markdown can't color, so teammate_id
  becomes a prefix emoji/colored-bullet convention (e.g. using colored
  circle emoji) or a `[color]` tag after the name.
- TeammateMessage: render as a `> blockquote` with a leading
  `**<color-emoji> teammate-name** (summary)` line.

**Template integration** (`claude_code_log/html/templates/transcript.html`):

- Include the new `teammate_styles.css` component.
- The main message renderer picks up TeammateMessage via the existing
  `format_content` dispatch — no per-site changes if the message_type
  field is wired correctly.

**Snapshot tests**:

- Update the existing snapshot tests to cover the fixture from PR 1
  rendered end-to-end.
- Verify key CSS classes appear in the output.

**Browser test**:

- Load the fixture-rendered HTML, assert two teammate-messages render with
  distinct color-coded left borders, and that a TaskList renders as a table.

**Estimated size**: ~400 LOC + ~200 LOC tests.

### PR 3 — `dev/teammates-polish`: stitch sidechain under tool_use + index integration

**Sidechain stitching under Agent/Task tool_use** (the "whole transaction
visibility" goal from #2197):

- Renderer change: when an Agent or Task tool_use has a resolved subagent
  session, place the subagent's DAG-line **below the tool_use** (not its
  child tool_result) in the rendered output, collapsed by default via
  `<details>`. The tool_result itself stays where it is DAG-wise, with the
  metadata surfaced structurally.
- Subsume the redundant first User sidechain message: the agent's first
  message is literally the Task prompt, which already appears in the
  tool_use rendering. Skip it in the subagent transcript.
- Applies equally to #90 async agents — same metadata shape. That issue
  should mostly fall out after this work.

**Index and session-header integration**:

- Team-badge on session headers when `teamName` is set.
- Teammate-badge (colored) on subagent session headers.
- Group subagent sessions visually under their parent Task tool_use in
  the combined transcript.
- In the project index, optionally show a "Team: N members" annotation for
  sessions that used a team.

**Estimated size**: ~250 LOC + polish-level tests.

---

## Concrete first steps when implementing PR 1

1. Branch is already created: `dev/teammates-parsing` off `main` at
   `1dd392d` (Release 1.2.0). This plan file is commit 1.
2. Commit 2 — models: add the 10 new `BaseModel`s and
   extend `TaskInput`/`TaskOutput`/`BaseTranscriptEntry`/`MessageMeta`.
   Keep additions strictly additive — no existing field changes.
3. Commit 3 — fixture: build
   `test/test_data/teammates/main.jsonl` and two `agent-*.jsonl` files from
   a trim of a real transcript. Verify it loads without errors under the
   existing (unmodified) code.
4. Commit 4 — tool_result metadata parser: regex-based extraction of
   `agentId:` / `worktreePath:` / `worktreeBranch:` / `<usage>` from the
   Markdown tail; strip the tail from the displayed body. Unit tests first.
5. Commit 5 — `<teammate-message>` XML parser and `TeammateMessage`
   factory. Unit tests for all shapes (single, multi, mixed, system).
6. Commit 6 — tool input/output factories for the six new tools.
7. Commit 7 — subagent linking in `converter.load_transcript`: agentId
   primary + prompt-hash fallback, feeding into `_integrate_agent_entries`.
8. Commit 8 — end-to-end test using the fixture.
9. `just ci` green, open PR targeting `main` with a clear summary that
   references this plan file.

## Open questions (none blocking)

- Where exactly should `AgentResultMetadata` live? Options:
  - As a dataclass field on `ToolResultMessage`.
  - As a separate `AgentMetadata` appended to `meta: MessageMeta`.
  - I'm leaning toward the first: it's tightly bound to the tool_result
    and the downstream rendering reads it from there.
- Naming for the synthetic sessionId of a teammate subagent:
  `{main}#teammate-{name}@{team}` is readable but long; `{main}#tm-{name}`
  is shorter. Decide during implementation — affects nothing outside
  rendering labels.

## Cross-references

- [dev-docs/dag.md](../dev-docs/dag.md) — DAG architecture, Phase C integration details.
- [work/phase-c-agent-transcripts.md](phase-c-agent-transcripts.md) — how
  regular sub-agents already integrate into the DAG today.
- Issue [#79](https://github.com/daaain/claude-code-log/issues/79) — the
  pre-existing sub-agent linking plumbing that this PR also fixes.
- Issue [#90](https://github.com/daaain/claude-code-log/issues/90) — async
  task agents; same metadata shape, should mostly fall out of this work.
- Issue [#91](https://github.com/daaain/claude-code-log/issues/91) — parent
  issue, contains extensive real-data snippets.
- PR [#115](https://github.com/daaain/claude-code-log/pull/115) — parallel-Task
  anchor preservation (merged prerequisite).
