Async-agents fixture
====================

Synthetic transcript exercising the async-agent flow (issue #90) added
by the Phase 1–3 work on top of the teammates feature.

**Layout**

```
eb000000-0000-4000-8000-000000000001.jsonl          # main session
eb000000-0000-4000-8000-000000000001/
  subagents/
    agent-cccc333333333333.jsonl                     # async agent sidechain
```

**What the fixture exercises**

- A `Task` tool_use with `run_in_background=true` and the canonical
  async tool_result shape (``Async agent launched successfully\n
  agentId: cccc333…\noutput_file: …``).
- A ``TaskOutput`` poll producing the standard
  `<retrieval_status><task_id><task_type><status><output>[Truncated…]`
  result body.
- A user entry whose `message.content` is a raw `<task-notification>`
  string with `<task-id>`, `<status>`, `<summary>`, `<result>`, and
  `<usage>` blocks plus the trailing
  `Full transcript available at: …` line.
- The notification's `<result>` body matches the **last sub-assistant
  text** in the agent's sidechain — so the Phase 3 fold drops that
  duplicate from the sidechain and renders it inside the spawning
  Task tool_result via `TaskOutput.async_final_answer`. The
  notification is then flagged `result_is_duplicate` so its body
  collapses to a backlink stub.

Data was sliced down from the canonical clmail-monk transcript
(`d602eb5f-…`) and trimmed to a minimal synthetic shape.
