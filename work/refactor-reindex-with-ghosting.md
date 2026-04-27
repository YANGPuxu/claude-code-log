# Refactor: replace `_reindex_filtered_context` with ghosting

Surfaced during the PR #132 (`dev/async-agents`) review pass. The
async-agents work hit three successive "remember to remap X" bugs
on a pass that deleted messages and reindexed:

1. Pair refs (`pair_first/middle/last`) — fixed by re-running
   `_identify_message_pairs` after the reindex.
2. `session_nav` anchors — fixed by rebuilding the nav after the
   reindex.
3. `TemplateMessage.ancestry` and stored backlink fields
   (`spawning_task_message_index`, `parent_message_index`) — fixed
   by switching to **ghosting**: the duplicate notification stays
   in `ctx.messages` with its original `message_index`, and the
   format-specific renderers return `""` for its title and body
   at LOW. The rendering loop's existing "skip empty messages"
   elision (HTML's `if title or html or msg.children:`, Markdown's
   `_render_message` returning `""` for no-title-no-content) drops
   the card from the visible output.

The async-agents PR landed with the targeted ghost. This doc
captures the broader question for a follow-up: the same fragility
applies to every existing reindex caller. The pattern keeps
growing as new index-bearing fields are added (most recently
`SessionHeaderMessage.parent_message_index` in PR #131).

## Where `_reindex_filtered_context` runs today

Two callers, both in `claude_code_log/renderer.py`:

1. **`_pair_skill_tool_uses`** (line ~2848) — drops 1–2 specific
   messages per Skill spawn (the slash-command body folded into
   the Skill `tool_use`'s `skill_body`, and the redundant
   "Launching skill: …" tool_result).
2. **`_filter_template_by_detail`** (line ~3002) — drops
   potentially many messages, by content type (`HIGH/LOW/MINIMAL/
   USER_ONLY` exclusion sets), sidechain status, or LOW-tool
   whitelist.

Both callers run **before** hierarchy build (`_build_message_hierarchy`,
line ~2024 sets `message.ancestry`), pair identification
(`_identify_message_pairs`, line ~800), and session navigation
prep (`prepare_session_navigation`, line ~789). So at reindex time:

- `pair_first/last/middle` are still `None` (defensive clearing
  in `_reindex_filtered_context` is just hygiene).
- `ancestry` arrays haven't been computed yet — they'll be built
  later from the post-reindex `message_index` values.
- `session_first_message` was populated at register time in
  `_render_messages`, **needs remap**.
- `SessionHeaderMessage.parent_message_index` was set at register
  time, **needs remap** (PR #131).
- `junction_forward_links` are populated between the two reindex
  calls (lines ~720–746); the second reindex must remap them.

## Current remap surface (what `_reindex_filtered_context` touches)

1. `message.message_index` + `message.content.message_index`
2. `pair_first/middle/last` (cleared)
3. `ctx.session_first_message` dict
4. `SessionHeaderMessage.parent_message_index`
5. `junction_forward_links` tuples (target index)

## Ghosting per caller

### `_pair_skill_tool_uses` — easy

Drops 1–2 specific messages per Skill spawn. Same shape as the
async-agents fix:

- A `consumed_by_skill_fold: bool = False` flag on
  `UserSlashCommandMessage` (and either the same flag on
  `ToolResultMessage`, or a `consumed_launching_skill_payload`
  twin).
- The slash-command and the launching-skill tool_result
  formatters return `""` when the flag is set.
- The render-loop elision drops both.
- The dropped pieces have no children at this point in the
  pipeline and aren't paired yet, so no further surgery is
  needed.

Estimate: ~10–15 lines, roughly the same shape as the async-agents
ghost.

### `_filter_template_by_detail` — invasive

Drops potentially many messages, sometimes whole subtrees (e.g.,
at MINIMAL: all tools, thinking, sidechain). Ghosting works in
principle but requires:

- **Tree-build skip + child grafting**: `_build_message_tree`
  must skip ghosts and graft their (real) children up to the
  next non-ghost ancestor. Otherwise the user sees empty cards
  with visible children attached underneath.
- **Pair-id skip**: `_identify_message_pairs` must skip ghosts.
  Otherwise a ghost `tool_use` gets paired with a real
  `tool_result`; the Markdown's `is_first_in_pair` walk would
  render an empty header followed by the paired body.
- **Render-loop elision flag check**: today the elision is
  "no title AND no content AND no children". With the broad
  filter, ghosts often have visible children, so the elision
  must also test the ghost flag (or the children-grafting in
  tree-build keeps the elision rule unchanged — the latter is
  cleaner).

Estimate: medium-sized refactor. Touches `_build_message_tree`,
`_identify_message_pairs`, possibly the render loops in both
HTML and Markdown.

## Suggested approach for the follow-up

1. Introduce `is_ghosted: bool = False` on `TemplateMessage`.
2. Move `_pair_skill_tool_uses` to set `is_ghosted` on the
   slash-command body + launching-skill tool_result instead of
   calling `_reindex_filtered_context`. Verify the existing
   skill-pair tests still pass (they cover the visible
   rendering, which is what matters).
3. Update `_build_message_tree` to skip ghosts and graft their
   children up to the next non-ghost ancestor.
4. Update `_identify_message_pairs` to skip ghosts.
5. Move `_filter_template_by_detail` to set `is_ghosted` instead
   of dropping. Re-run the per-detail-level snapshot suite.
6. Once both callers are migrated, delete
   `_reindex_filtered_context`. The remap fields it touched
   (`session_first_message`, `parent_message_index`,
   `junction_forward_links`) all stop needing maintenance.

## Alternative: scoped ghost flags per content type

If a project-wide `is_ghosted` field feels too heavyweight,
each callsite can use a content-type-scoped flag (the pattern
already used for `TaskNotificationMessage.result_is_duplicate`
and what the skill-fold ghost would add). The trade-off:

- **Pro**: no new field on `TemplateMessage`; each ghost reason
  is self-documenting in the type system.
- **Con**: every render-loop check is bespoke; adding a new
  ghosting reason requires touching the render loops.

The unified `is_ghosted` flag scales better for `_filter_template_
by_detail` because that pass needs to ghost dozens of message
types based on a runtime detail level — encoding the decision
on each content type would require duplicate logic in each
formatter.

## Issue tracking

- [PR #131](https://github.com/daaain/claude-code-log/pull/131) —
  added the `parent_message_index` remap to
  `_reindex_filtered_context`. The growing remap surface is the
  motivating evidence for this refactor.
- [PR #132](https://github.com/daaain/claude-code-log/pull/132) —
  async-agents support; landed the targeted ghost for
  `TaskNotificationMessage` at LOW.

## Out of scope for this work item

- Sidechain duplicate cleanup
  (`_cleanup_sidechain_duplicates`) does **not** call
  `_reindex_filtered_context` — it mutates `parent.children`
  in place without touching `ctx.messages`. No ghosting needed.
- The pre-render filter `_filter_by_detail` (entries level,
  before `_render_messages`) is a different layer and not part
  of this remap surface.
