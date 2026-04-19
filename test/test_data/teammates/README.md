Teammates fixture
=================

Synthetic transcript exercising the experimental teammates feature
(`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`, Claude Code 2.1.32+).

**Layout**

```
ef000000-0000-4000-8000-000000000001.jsonl          # main (team-lead) session
ef000000-0000-4000-8000-000000000001/
  subagents/
    agent-aaaa111111111111.jsonl                     # alice teammate
    agent-bbbb222222222222.jsonl                     # bob teammate
```

**What the fixture exercises**

- All six teammate-management tools (TeamCreate, TaskCreate×2, TaskUpdate,
  TaskList, SendMessage, TeamDelete).
- Two Task tool_use/tool_result pairs that spawn named teammates,
  each with a tool_result metadata tail
  (`agentId:`, `worktreePath:`, `worktreeBranch:`, `<usage>`).
- Alice's Task tool_result also carries the structured
  `toolUseResult.agentId` so the existing converter linking works.
- Bob's Task tool_result has no `toolUseResult.agentId` — his subagent
  file is matched by hashing the Task prompt against the
  `<teammate-message teammate_id="team-lead">` body in the agent JSONL's
  first entry (prompt-hash fallback path).
- Two user entries with `<teammate-message>` blocks: a single-block case
  (alice, with summary) and a mixed case (alice + bob + a system
  termination notice).

Data was derived by studying real teammate transcripts on the author's
system (notably the `clmail-monk` and `experiments/worktrees` sessions)
and trimmed to a minimal synthetic shape.
