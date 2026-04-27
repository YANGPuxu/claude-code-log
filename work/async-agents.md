# Async agents — implementation plan (#90)

Branch: `dev/async-agents`
Test fixture:
`/home/cboos/.claude/projects/-home-cboos-Workspace-BCT-Common-TechVision-ClaudeCode-plugins-clmail-monk/d602eb5f-0f14-4a11-b0dd-b6f3af48833c.jsonl`
(2.3 MB main + several `subagents/agent-*.jsonl`).

The `a8b740b` task id called out in the issue is in this fixture.

## Data shapes (verified against the fixture)

### Async Task spawn

`Task` tool_use with `run_in_background: true`. Standard input keys
(`description`, `prompt`, `run_in_background`, `subagent_type`).

The matching tool_result is plain text, e.g.:

```
Async agent launched successfully.
agentId: a8b740b (internal ID - do not mention to user. Use to resume later if needed.)
The agent is working in the background. You will be notified automatically when it completes.
Continue with other tasks.
output_file: /tmp/claude-1000/.../tasks/a8b740b.output
To check progress before completion (optional), use Read or Bash tail on the output file.
```

The `agentId:` line is the join key. Below the spawn there is a sidechain
(loaded from `subagents/agent-{agentId}.jsonl`) whose last sub-assistant
holds the actual work output.

### `TaskOutput` polling tool

Polling tool that retrieves status / output of an async task.

Input: `{task_id, block, timeout}`.

Output (string content): an XML-tagged block:

```
<retrieval_status>success</retrieval_status>
<task_id>a5de609</task_id>
<task_type>local_agent</task_type>
<status>completed</status>
<output>
[Truncated. Full output: /tmp/.../tasks/a5de609.output]
... a few KB of the actual transcript content ...
</output>
```

The `<output>` block is the *agent's* full transcript truncated, which is
useless to surface in our HTML (we already render the agent's transcript
inline as a sidechain).

### `<task-notification>` user message

When the async agent completes, Claude Code injects a user-typed entry
into the trunk session, content shaped like:

```xml
<task-notification>
<task-id>a8b740b</task-id>
<status>completed</status>
<summary>Agent "Analyze relay.py coverage gaps" completed</summary>
<result>
... markdown of the agent's final response ...
</result>
<usage>total_tokens: 23099
tool_uses: 2
duration_ms: 15506</usage>
</task-notification>
Full transcript available at: /tmp/.../tasks/a8b740b.output
```

The `<result>` content **duplicates** the last sub-assistant message in
the spawned agent's sidechain.

## Plan

### Phase 1 — Typed models + parsers

- **`models.py`**:
  - `TaskOutputInput(BaseModel)`: `task_id: str`, `block: bool`,
    `timeout: int`. `model_config = {"extra": "allow"}`.
  - `TaskOutputResult` dataclass: `retrieval_status: str`,
    `task_id: str`, `task_type: str`, `status: str`,
    `output: Optional[str]`, `output_truncated: bool`,
    `output_file: Optional[str]`, `raw_text: Optional[str]`.
  - `TaskNotificationUsage` dataclass: `total_tokens, tool_uses,
    duration_ms` (all `Optional[int]`).
  - `TaskNotificationMessage(MessageContent)`:
    `task_id, status, summary, result_text, usage,
    transcript_path` (all the obvious types). `message_type =
    "task_notification"`. Mirrors `TeammateMessage`'s shape.
  - Add to ToolInput / ToolOutput unions.

- **Factories**:
  - `factories/tool_factory.py`:
    `TOOL_INPUT_MODELS["TaskOutput"] = TaskOutputInput`,
    `TOOL_OUTPUT_PARSERS["TaskOutput"] = parse_taskoutput_output`.
    The parser strips the `<output>` block (we don't need it) but
    captures the metadata.
  - `factories/task_notification_factory.py` (new): regex parser for
    `<task-notification>...</task-notification>` blocks + the trailing
    `Full transcript available at: ...` line. Returns a typed
    `TaskNotificationMessage` content. Mirrors `teammate_factory`.
  - `factories/user_factory.py`: hook
    `create_task_notification_message` ahead of the teammate / default
    text dispatch — the content starts with a literal
    `<task-notification>` so detection is cheap.

### Phase 2 — Rendering

- **HTML (`html/teammate_formatter.py` or a new
  `html/async_formatter.py`)**:
  - `format_task_notification_content(content)`:
    - Title comes from `title_TaskNotificationMessage` (Phase 2 also
      adds that to the renderer): `🔄 Async result • <summary>` (or
      similar; bike-shed).
    - Body = `<dl>` with `Task ID`, `Status`, `Tokens`,
      `Tool uses`, `Duration` rows + collapsible markdown for
      `result_text`. JSON-body heuristic from teammates applies.
  - `format_taskoutput_input` / `format_taskoutput_output`:
    minimal cards — input shows just the `task_id`; output shows
    `Status`, `Type`, drops `<output>` content entirely (link to the
    `output_file` path if useful).
  - Async-spawn hint on Task tool_use title: when
    `input.run_in_background == True`, append a small `<span
    class="task-async-hint">[async]</span>` after the description
    or change the emoji (`⏳ Task` instead of `🔧 Task` perhaps).

- **Markdown** (`markdown/renderer.py`): mirror the HTML — frontmatter-
  style key:value list + collapsible `<details>` for result; minimal
  TaskOutput; async hint on Task title.

- **CSS**: style the new `.task-notification-card` /
  `.task-output-card` / `.task-async-hint` modestly. Reuse existing
  palette tokens (`--cc-blue` for async, similar to TaskUpdate).

### Phase 3 — Stitching & dedup

- **Map `agentId → notification_msg_index`**: walk
  `ctx.messages` once after rendering, build a session-scoped map so
  the spawning Task tool_use can render a forward-link "→ Async
  result" pointing at the notification's `d-N` anchor.

- **Fold last sub-assistant into spawning tool_result**: the spawn's
  tool_result body currently shows just "Async agent launched
  successfully\n…". Reuse the result content from the sidechain's
  last sub-assistant (the actual answer). Two options:
  1. Inline the markdown rendering into the tool_result card as a
     "Result" section.
  2. Move the last sub-assistant message structurally to be the
     immediate child of the tool_result, so dedup-style logic can
     hide it from the sidechain when it appears as the result.
  Option 1 is less intrusive; option 2 is cleaner per parentUuid
  semantics. Lean toward option 1 first, see if it reads well.

- **Notification → backlink-only**: once the result is folded into
  the spawn, render the `<task-notification>` user message as a
  collapsed backlink ("↑ Async result for Task #N • <summary>") that
  preserves the uuid chain but doesn't duplicate the body.
  Threshold: if `result_text` matches (after `_normalize_for_dedup`)
  the last sub-assistant text we already folded, hide the body.

### Phase 4 — Tests

- **Unit**: `test_task_notification_parser` covering happy path,
  missing fields, nested markdown with backticks, JSON-shaped
  result, malformed block (no closing tag).
- **Unit**: `test_taskoutput_parser` covering truncated +
  non-truncated outputs.
- **Integration regression**:
  - Add a (small, anonymized) fixture under
    `test/test_data/real_projects/-clmail-monk/` — clmail-monk source
    .jsonl is 2.3 MB so we'd want to slice down to the relevant
    spawn / TaskOutput / notification triples. OR a synthetic
    minimal fixture under `test/test_data/async_agents/`.
  - `TestAsyncAgents` class with: parser detects notification,
    rendering shows minimal TaskOutput, notification body is folded
    when redundant, backlink reaches notification.
- **Snapshot refresh** for any fixture that triggers the new
  rendering.

## Open questions / bike-shedding

- What title for the notification? Options: `User (async result)`,
  `🔄 Async result`, `📬 Async result`. Currently leaning `🔄`.
- Should the minimal TaskOutput show a clickable path to the
  `output_file`? Filesystem paths are local, so a non-link rendering
  (just visible code span) is probably right.
- For the dedup decision — if the last sub-assistant text doesn't
  match the notification's `<result>` (rare but possible: agent
  edited file, no markdown response), we should keep the
  notification body so nothing is lost.

## Out of scope (separate work)

- Surfacing TaskOutput's `<output>` truncated transcript in any
  useful form (we already have the real transcript inline).
- Cross-PR teammate / async interaction beyond what the existing
  `_relocate_subagent_blocks` already does — async sidechains
  thread the same way.

## Cross-references

- Issue: [#90](https://github.com/daaain/claude-code-log/issues/90)
- Trilogy doc that mentioned this as future work:
  [`dev-docs/teammates.md`](../dev-docs/teammates.md) §10.1.
- Subagent threading machinery
  (`_integrate_agent_entries`,
  `_relocate_subagent_blocks`,
  `_collect_agent_anchors`)
  in `claude_code_log/converter.py`,
  `claude_code_log/renderer.py`, and `claude_code_log/dag.py`.
