# Simplification Plan: converter.py & renderer.py

> Produced by a multi-agent analysis workflow (6 area-maps → 33 proposed
> opportunities → adversarial verification → synthesis). Read-only analysis;
> no code changed. Implementation proceeds via the worktree-actors flow
> (one branch per opportunity, monk reviews, main merges progressively).

## 0. Live status

> Working log of the implementation effort, updated as branches land.
> Status-doc edits go **direct to `main`** (doc-only); code changes go
> through PRs reviewed by monk + CodeRabbit and merged by the maintainer.
> Implementation runs via the worktree-actors flow: **alice** develops each
> branch locally, reviews with **monk**, iterates, then pushes a PR; **main**
> coordinates and watches each PR's CodeRabbit/CI; **bob** is the overflow
> implementer.

| Opp | Branch (`wf/simplify/…`) | PR | State |
|-----|--------------------------|-----|-------|
| 5 — drop-progress-repair | `drop-progress-repair` | [#175](https://github.com/daaain/claude-code-log/pull/175) | ✅ merged |
| 2 — away-summary-rule | `away-summary-rule` | [#176](https://github.com/daaain/claude-code-log/pull/176) | ✅ merged |
| 4 — extract-junction-link-pass | `extract-junction-link-pass` | [#177](https://github.com/daaain/claude-code-log/pull/177) | ✅ merged |
| 3 — fix-rendering-arch-doc | `fix-rendering-arch-doc` | [#178](https://github.com/daaain/claude-code-log/pull/178) | ✅ merged |
| §7 — detail-visibility-method | `detail-visibility-method` | [#181](https://github.com/daaain/claude-code-log/pull/181) | ✅ merged |
| 1 — pagination-token-dedup | `pagination-token-dedup` | [#182](https://github.com/daaain/claude-code-log/pull/182) | ✅ green — monk ✅, CI green, CodeRabbit clean (awaiting maintainer merge) |
| 6 — factor-session-headers | `factor-session-headers` | [#183](https://github.com/daaain/claude-code-log/pull/183) | monk ✅, local `just ci` green; CI+CodeRabbit deferred to merge-time¹ |
| 7 — branch-label-source | `branch-label-source` | _pending_ | 🔄 in progress (alice, stacked on opp 6) |

> ¹ **Stacked-PR CI/CodeRabbit nuance:** the test workflow triggers only on
> `pull_request → main`, and CodeRabbit skips reviews on PRs whose base isn't
> `main` ("Review skipped" on #183). So a stacked PR gets **no** CI/CodeRabbit
> while its base is a feature branch. As the parent merges, GitHub auto-retargets
> the child PR's base to `main`, which then fires its full CI + CodeRabbit on the
> clean (atomic) diff. So progressive merging gives each PR its automated review
> at merge-time — opp 1 (#182, base `main`) is the only one auto-validated now.
| 8–12 — Wave C / D | — | — | ⏳ not started |

**Current track (sequential):** opp 1 → opp 6 → opp 7. Branches are **stacked** —
each PR targets the previous branch (`--base`) until that branch merges, so
CodeRabbit diffs only the new change. opp 1 lives in `converter.py`; opp 6/7 in
`renderer.py` and overlap the branch-header block, so 7 must follow 6.

## 1. Executive summary

`converter.py` (3189 lines) and `renderer.py` (4618 lines) are hard to follow for two structural reasons. First, **session metadata is derived independently in at least four-to-five places** that all run the same prologue (build a uuid→session map, resolve `SummaryTranscriptEntry.leafUuid`→session, overlay ai-titles, group by `get_parent_session_id`, filter warmup, accumulate previews/tokens): `_update_cache_with_session_data`, `_build_session_data_from_messages`, `_collect_project_sessions` (all in `converter.py`), plus an inline aggregate loop in the index no-cache fallback, and the renderer's own `prepare_session_summaries`/`prepare_session_ai_titles`/`prepare_session_team_names`/`_collect_session_info`. These copies already drift (the pagination fallback double-counts tokens because it lacks the `requestId` dedup the cache path has), and the code comments literally say "Mirrors X" / "matches the logic from renderer.py exactly" — a hand-maintained invariant with no enforcement. Second, **`renderer.py::generate_template_messages` runs ~20 strictly-ordered in-place passes over a flat `list[TemplateMessage]`**, several of which re-derive session/branch structure the `SessionTree` (dag.py) already knows authoritatively — most acutely the `current_render_session` mutable loop variable in `_render_messages`, which re-discovers branch boundaries by walking the message stream.

The **central session-vs-rendering cross-dependency** is this: `dag.py` is the clean, authoritative session-resolution layer (DAG-lines, junction points, `{trunk}@{uuid}` branch pseudo-sessions, `{trunk}#agent-{id}` agent pseudo-sessions), but the renderer re-implements a *shadow* of it over `TemplateMessage`s — re-discovering branch membership, re-composing branch labels in four sites, re-deriving session summaries the converter already computed for the cache, and stamping positional `message_index` cross-references that then must be remapped by `_reindex_filtered_context` whenever the detail filter drops a message. The highest-value simplifications collapse the duplicated session-metadata derivation into shared helpers and read branch grouping from the `SessionTree` instead of re-discovering it — without touching the DAG/tree ordering that is authoritative.

## 2. Architecture-as-built map

**Parsing side (converter.py), directory mode** — `convert_jsonl_to` → `ensure_fresh_cache` → `load_directory_transcripts`:

```
per-file load_transcript ─→ prompt-hash subagent link ─→ recursive agent-file load+splice ─→ cache write
        │
        ├─ _scan_progress_chains ─→ _repair_parent_chains   (re-reads files; heals dropped-progress parentUuid gaps)
        ├─ _integrate_agent_entries  (re-parent sidechain roots to anchor; rewrite sessionId → {trunk}#agent-{id})
        ├─ _scan_sidechain_uuids     (re-reads */subagents/*.jsonl AGAIN; orphan-warning suppression)
        └─ build_dag_from_entries ─→ traverse_session_tree ─→ dag_ordered list (+ non-DAG tail)
                                                                      │
                            deduplicate_messages (content-key) ◄──────┘  (post-traverse, post-date-filter)
```

Hard ordering: prompt-hash-link → agent-load → repair → integrate → build_dag → traverse. `_integrate_agent_entries` MUST precede `build_dag` (dag.md "Expected Root Types"). `*/subagents/*.jsonl` is read **twice** (progress scan + sidechain-uuid scan).

**Session-metadata derivation (the tangle)** — same prologue reimplemented in:
- `_update_cache_with_session_data` (canonical: `SessionCacheData` + project aggregates, `requestId` token dedup, cwd, warmup+empty filter)
- `_build_session_data_from_messages` (pagination cache-miss fallback: NO dedup, NO empty filter, NO cwd — drift source)
- `_collect_project_sessions` (index nav `list[dict]`; ai-title folded into `summary`)
- index no-cache fallback inline loop (token/timestamp/team_name re-derivation)
- renderer `prepare_session_summaries`/`_ai_titles`/`_team_names` + `_collect_session_info`

`SessionCacheData` is the de-facto schema; staleness (`is_page_stale`/`is_html_stale`) keys off `sessions.message_count` + `last_timestamp` written by the canonical path.

**Rendering side (renderer.py)** — `generate_template_messages` ordered passes:

```
prepare summaries/ai-titles/team-names  +  _extract_session_hierarchy (from SessionTree)
        ↓
_filter_messages (structural) ─→ _filter_by_detail (pre-render, entry-level)
        ↓
_collect_session_info (Pass 1)
        ↓
_render_messages (Pass 2): create+register TemplateMessages (message_index = position),
        synthesize trunk + branch SessionHeaderMessages, assign render_session_id
        via the current_render_session LOOP VARIABLE (re-discovers branch boundaries)
        ↓
_pair_skill_tool_uses ─→ _reindex_filtered_context  (FIRST reindex)
        ↓
junction-forward-link population (INLINE ~55 lines) + _enrich_branch_titles (re-scan fixup)
        ↓
[optional] _filter_template_by_detail ─→ _reindex_filtered_context  (SECOND reindex)
        ↓
prepare_session_navigation
        ↓
_reorder_session_template_messages → _identify_message_pairs → _reorder_paired_messages
        → _relocate_subagent_blocks → _build_message_hierarchy → _mark_messages_with_children
        → _build_message_tree → _cleanup_sidechain_duplicates
        ↓
six independent trailing link/metadata passes (teammate colors, task metadata,
        async/tool-use notifications, cron, task_id consumers)
```

**Where session logic tangles into rendering:** (A) `render_session_id` re-derived by loop variable instead of read from `SessionTree`; (B) branch-label/preview composed in four sites that must agree (`_branch_label`, `_enrich_branch_titles`, nav, junction-link block); (C) positional `message_index` cross-references forcing `_reindex_filtered_context`; (D) the converter re-derives the same session summaries the renderer does.

## 3. Simplification opportunities, prioritized

Sorted by value (high impact + low adjusted-risk + low/medium effort first). Risk = verifier's adjusted_risk.

| # | ID | Title | Impact | Risk | Effort | Verdict basis |
|---|----|-------|--------|------|--------|---------------|
| 1 | `cache-fix-pagination-token-dedup` | Add `requestId` token dedup to `_build_session_data_from_messages` (standalone correctness) | low | low | low | solid |
| 2 | `detail-consolidate-away-summary-rule` | Co-locate the `away_summary` HIGH-visibility rule onto `AwaySummaryMessage.detail_visibility` | low | low | low | solid |
| 3 | `renderer-fix-stale-rendering-arch-doc` | Correct `rendering-architecture.md` §5 / `message-hierarchy.md` stale names + line refs | low | low | low | solid |
| 4 | `renderer-extract-junction-link-pass` | Extract inline junction-forward-link block into `_link_junction_forwards(ctx)` | low | low | low | solid |
| 5 | ~~`conv-fuse-progress-and-sidechain-scans`~~ → `conv-drop-progress-repair` | **SUPERSEDED** by the investigation: delete the whole `_scan_progress_chains`/`_repair_parent_chains` trio (vestigial); `_scan_sidechain_uuids` is then the only aux scan, nothing to fuse | medium | low | low | confirmed (1916 tests pass neutered) |
| 6 | `crux-build-headers-from-tree-not-loop` (Phase 1 only) | Factor trunk/branch header construction out of `_render_messages` into `_build_trunk_header`/`_build_branch_header` | medium | low | medium | risky→Phase1 safe |
| 7 | `crux-unify-branch-label-source` | Compute branch preview once from the DAG-line; delete `_enrich_branch_titles` | medium | low | medium | solid (drop depends_on) |
| 8 | `conv-reuse-summary-extractors` (narrowed) | Replace converter's inline summary+ai-title blocks with `prepare_session_summaries`/`prepare_session_ai_titles` (NOT team_name) | medium | medium | low | risky→narrow |
| 9 | `cache-extract-session-scan-core` | Extract one pure `compute_session_data` + `compute_project_aggregates`; route the cache, fallback, index, and inline-aggregate sites through it | high | medium | medium | risky→needs char. tests |
| 10 | `conv-unify-single-and-directory-load` (build_tree=False only) | Extract the shared scan/repair/integrate 3-line sequence; single-file stays raw-ordered | medium | low | low | risky→half only |
| 11 | `crux-derive-render-session-id-from-dag` | Replace `current_render_session` loop variable with a `uuid→render_session_id` map from `SessionTree` | high | medium | medium | risky→unify header+tag |
| 12 | `detail-delete-reindex` | Delete `_reindex_filtered_context` after both callers stop deleting from `ctx.messages` | medium | medium | low | risky (gated) |

### Rejected / deferred (verifier marked illusory, or near-zero value)

- **`renderer-fuse-hierarchy-mark-tree`** — false premise: only `_mark`/`_build_tree` build the index map, not `_build_message_hierarchy`; saves one dict build, loses per-phase timing granularity. Drop.
- **`conv-progress-chain-from-parsed-entries`** — illusory + adds a cache bug; progress entries with uuid+sessionId survive as Passthrough, so the "dropped-progress" map is essentially empty on real data. **Replace with**: investigate *deleting* the `_scan_progress_chains`/`_repair_parent_chains` trio entirely (vestigial since #99) — tracked as an open question, not a branch.
- **`conv-clarify-dual-dedup`** — illusory: DAG uuid-dedup and content-key dedup own disjoint classes (already documented); narrowing would regress single-file/export paths. Drop (at most a one-line doc cross-ref).
- **`renderer-data-driven-pairing-rules`** — illusory: unifies the easy half, leaves the hard `_try_pair_by_index` guard logic; the marker dispatch reintroduces branching. Drop.
- **`renderer-merge-trailing-link-scans`** — illusory: the "all six order-independent" premise is false (`_link_tool_use_notifications` depends on `_link_async_notifications`); merging widens scope and loses timing. Drop.
- **`renderer-assert-pipeline-preconditions`** — illusory: the proposed "session-grouped" assert would false-fire on every subagent fixture (hierarchy build runs *after* `_relocate_subagent_blocks`); ordering already in docstrings. Drop.
- **`crux-extract-link-pass-scaffold`** — illusory: only ~1.5 of 4 passes fit the proposed scaffold; the shared idiom is a twice-repeated two-liner. Drop.
- **`crux-separate-affordance-caches-from-context`** — illusory: formatters already snapshot these dicts off `ctx`; the nesting step is pure churn that breaks a test assertion. Drop.
- **`crux-consolidate-prepare-scans`** — illusory: summaries+ai-titles already merged into one dict at the call site; dicts are needed at different pipeline stages; can't hang on `SessionDAGLine` (DAG skips Summary/AiTitle entries). Drop.
- **`crux-build-headers-from-tree-not-loop` Phase 2** — risky and mis-specified (a "pass over the header set" can't resolve branch `attachment_uuid` content-message indices). Defer; keep only Phase 1 (#6).
- **`detail-move-template-filter-to-tree`** — illusory + would break behavior: nav/counts/links run *after* the proposed prune point, so descendant counts and backlink anchors would dangle. Drop.
- **`detail-ghost-skill-fold`** — illusory: the ghosted level-1 slash body adopts the following assistant turn as a child after `_build_message_hierarchy`, so the elision rule keeps it (renders a bare card). Drop as standalone; only viable inside a full ghosting migration with a hierarchy-skip pass.
- **`detail-index-skill-tool-results`** — risky + near-zero value: a single-value map would break the multi-result-per-tool_use_id preservation tests; needs a list-valued map; not a measured hotspot. Defer.
- **`cache-pagination-single-source`** — illusory: the else-arm is already unreachable in directory pagination; deleting `_build_session_data_from_messages` breaks unlisted `test_dag_integration.py` tests. Fold into #9.
- **`cache-index-fallback-via-shared`** — risky: would silently drop date-range filtering from index cards (cache is unfiltered, inline loop runs on date-filtered messages). Defer until #9 clarifies the data source.
- **`conv-relocate-summary-extractors`** — illusory: converter already imports from renderer; relocating adds a module + re-exports for no net reduction. Drop.
- **`renderer-ghost-skill-fold-reindex` / `renderer-ghost-detail-filter-reindex`** — the larger ghosting migration; both risky and mis-specified as scoped. Deferred to the open-questions section as a maintainer-call epic, superseded by the simpler #11/#12 path where applicable.

## 4. Sequenced, mergeable branches

Twelve branches in dependency order. Each is independently reviewable and mergeable alone. Branch from current `main` (`git checkout -b <branch> main`).

### Wave A — standalone, no dependencies (parallelizable)

**`dev/simplify-pagination-token-dedup`** (opp 1)
- **Does:** Add a `seen_request_ids` guard to the token-accumulation block in `_build_session_data_from_messages` (converter.py:1240-1257). Critical nuance: dedup *only when a requestId is present*; still count usage for assistant entries that have *no* requestId (do NOT mirror the cache guard literally — that would drop un-keyed usage).
- **Safe alone:** Token totals are not part of the staleness comparison key (that uses `message_count`/`last_timestamp`), so no invalidation churn. Pure correctness alignment with the canonical path.
- **Tests:** `test/test_pagination.py` (token summaries), `test_dag_integration.py::test_agent_messages_coalesced_into_parent_session` (asserts `total_input_tokens==20`; passes unchanged since helper sets unique requestIds). Serial snapshot check.
- **Spirit:** Brings fallback in line with the documented requestId-dedup invariant.

**`dev/simplify-away-summary-rule`** (opp 2)
- **Does:** Set `detail_visibility = DetailLevel.HIGH` on `AwaySummaryMessage` (models.py). Remove `AwaySummaryMessage` from `_LOW_EXCLUDE_CLASSES` (renderer.py:3179) since the class-attr short-circuit makes it dead. Reduce the pre-render whitelist comment to a one-line cross-ref. Add `ClassVar` to the models.py typing import.
- **Safe alone:** `_content_visible_at` already prefers the class attr; outcome is mathematically identical across all five levels. `ClassVar` is excluded from dataclass fields, so constructors are unaffected.
- **Tests:** `test/test_away_summary.py::TestAwaySummaryDetailLevels`, `test_detail_levels.py`, per-detail snapshots (unchanged).
- **Spirit:** Single source of truth via the documented `detail_visibility` migration mechanism (plugins.md §6).

**`dev/simplify-fix-rendering-arch-doc`** (opp 3)
- **Does:** Correct `rendering-architecture.md` §5 (`_process_messages_loop` → `_render_messages`, refresh the phase list) and `message-hierarchy.md` references. Replace hardcoded line numbers with function-name anchors. Keep §5's 4-phase pedagogical model; add the extra reordering/link passes as a short addendum, not a 20-step transcription.
- **Safe alone:** Docs only; no code/test impact.
- **Tests:** None.
- **Spirit:** Brings as-built reference in sync (CLAUDE.md: code is authoritative).

**`dev/simplify-extract-junction-link-pass`** (opp 4)
- **Does:** Move the ~55-line inline fork-point linking block (renderer.py:737-791) into a module-level `_link_junction_forwards(ctx)`, wrapped in `log_timing`. Keep the call in place (before the detail reindex). `uuid_to_msg`/`idx_to_msg`/`fork_msg` become locals.
- **Safe alone:** Fully self-contained (inputs: `ctx.junction_targets`/`ctx.messages`/`ctx.session_first_message`; outputs: writes to `fork_point_preview`/`junction_forward_links`). Mechanical, behavior-neutral.
- **Tests:** Fork/branch snapshot tests, `test_dag.py` (stay green).
- **Spirit:** Restores the uniform "one named pass per step" shape; no logic change.

**`dev/simplify-fuse-aux-scans`** (opp 5)
- **Does:** Introduce `_scan_directory_aux(directory)` walking each file once, returning `(progress_chain, sidechain_uuids)`. Replace the two call sites in `load_directory_transcripts`. Keep the `"progress" not in line` pre-filter for top-level `*.jsonl`; glob progress from both top-level and `*/subagents/*.jsonl` but uuids only from subagent files. Single-file mode keeps progress-only `_scan_progress_chains`.
- **Safe alone:** Reads the same unchanging file set once instead of twice; `_integrate_agent_entries` is purely in-memory so moving the sidechain scan earlier is identical. Same maps fed downstream.
- **Tests:** `test_dag_silent.py` (orphan suppression), `test_dag.py`. Add a regression through `load_directory_transcripts` exercising the fused path. `_scan_progress_chains` kept verbatim → `test_dag_integration.py` stays green.
- **Spirit:** Pure I/O collapse; no DAG/tree change.

### Wave B — renderer header/label cleanup

**`dev/simplify-factor-session-headers`** (opp 6, Phase 1 only)
- **Does:** Extract the trunk-header block (renderer.py:3952-3979) and branch-header block (3791-3858) from `_render_messages` into `_build_trunk_header(sid, hier, summaries, team_names)` and `_build_branch_header(...)`. `_render_messages` still *calls* them in place; header *registration* (positional placement in `ctx.messages`) stays in the loop to preserve `#msg-d-{N}` anchor stability.
- **Safe alone:** Pure locality extraction, snapshot-identical. No dependency on the DAG-derivation branch. Does NOT attempt Phase 2 (the mis-specified pre-pass).
- **Tests:** `test_session_export.py`, `test_combined_transcript_link.py`, snapshots (unchanged).
- **Spirit:** Headers carry the same fields from the same `SessionTree`; ordering/placement unchanged.

**`dev/simplify-branch-label-source`** (opp 7) — *depends on: none (drop the proposed depends_on)*
- **Does:** When building the branch `SessionHeaderMessage`, scan the branch DAG-line's uuids for the first user entry with text (via `extract_text_content`, same path that handles slash commands) and set preview/title once. Remove `_enrich_branch_titles` (renderer.py:1033-1101) and its call (734). `_branch_label` stays the single label formatter. Add the branch's `DagLine.uuids` to `session_hierarchy` (small plumbing) or build a uuid→entry map. Preserve the "do not widen when a real preview already exists, incl. 5-char `/exit`" precedence rule (scan only when empty).
- **Safe alone:** Nav/fork-box/forward-links already read `SessionHeaderMessage.preview`, so computing it correctly once makes the fixup pass dead code without touching consumers. Removes the fragile `is_sidechain` guard and an ordering constraint.
- **Tests:** `test_session_export.py`, fork/branch snapshots. Add a fixture where a branch starts with an assistant turn (locks the behavior enrich handled) and one starting with a slash command (locks #129 precedence in `test_utils.py:606-666`). Update `dag.md:106` and the comment at renderer.py:1299-1306.
- **Spirit:** Same `Branch • <uuid8> • <preview>` string, single-sourced from the DAG-line rather than guessed-then-patched.

### Wave C — converter session-metadata consolidation (the core)

**`dev/simplify-reuse-summary-extractors`** (opp 8, narrowed) — *depends on: none*
- **Does:** Replace ONLY the inline summary+ai-title blocks in `_update_cache_with_session_data` and `_build_session_data_from_messages` with `prepare_session_summaries(messages)` and `prepare_session_ai_titles(messages)`. For `_collect_project_sessions`, only consolidate if the ai-title overlay is reproduced exactly (`summaries.update(prepare_session_ai_titles(messages))` — do NOT introduce a separate dict, which would drop ai-title precedence). **Do NOT touch team_name** (it rides the existing grouping loop coalesced by `get_parent_session_id`; `prepare_session_team_names` keys by raw sessionId and is not byte-equivalent, and extracting it adds a pass).
- **Safe alone:** Summary/ai-title only ever land on real (non-agent) sessions, so raw==parent keying is equivalent. Byte-identical output.
- **Tests:** `test_ai_title.py` (precedence), converter cache tests, projects-index nav tests, snapshots, `test_teammates_parsing.py` (confirm team_name untouched).
- **Spirit:** Single-sources the leafUuid→summary precedence invariant the comments already flag as hand-maintained.

**`dev/simplify-session-scan-core`** (opp 9) — *depends on: `dev/simplify-reuse-summary-extractors`, `dev/simplify-pagination-token-dedup`*
- **Does:** Extract a pure `compute_session_data(messages, *, include_cwd) -> dict[str, SessionCacheData]` + `compute_project_aggregates(messages)` from `_update_cache_with_session_data`. Rewrite `_update_cache_with_session_data` as a thin wrapper (compute + write to cache). Route `_build_session_data_from_messages` and the index no-cache inline-aggregate loop (converter.py:2912-2978) through them. Keep the `_build_session_data_from_messages` symbol (tests import it). Keep `_collect_project_sessions` as an explicit projection adapter (preserve `timestamp_range` formatting, the `[No user message found...]` placeholder, ai-title-into-summary collapse).
- **Critical reconciliations (make explicit, not invisible):** (a) the requestId token *gate* zeroes tokens for no-requestId assistants in the cache path — pick the cache behavior as canonical and document it; (b) `PassthroughTranscriptEntry` is excluded by the fallback but counted by the cache path, which affects `message_count` (the staleness key) and page assignment — pick one exclusion set and document the count change.
- **Safe alone (with guards):** Write *characterization tests* pinning current `message_count`, token totals, and session sets for each call site BEFORE refactoring, so divergences are surfaced as deliberate decisions.
- **Tests:** `test_pagination.py` (page assignment — most at risk), `test_cache.py`, `test_dag_integration.py` (lines 1164/1369 message_count + token assertions), `test_ai_title.py`, `test_teammates_parsing.py`. Snapshot suite serial.
- **Spirit:** Does not touch DAG/tree; consolidates leaf-metadata derivation. `get_parent_session_id` coalescing, warmup/empty filtering, and the title fallback chain move verbatim into the shared function. Cache schema + mtime/schema-version invalidation untouched.

**`dev/simplify-shared-prepare-pipeline`** (opp 10, `build_tree=False` only)
- **Does:** Extract the verbatim 3-line `_scan_progress_chains` → `_repair_parent_chains` → `_integrate_agent_entries` sequence (shared by `load_directory_transcripts` ~761-766 and the single-file branch ~1584-1588) into one `_prepare_messages(messages, *source_paths)` helper. **Drop the `build_tree=True` variant** — single-file mode must keep passing raw-ordered messages with `session_tree=None` so ordering/warning behavior is unchanged.
- **Safe alone:** `_scan_progress_chains` already accepts a file or directory. Relocates existing calls in existing order; no behavioral change.
- **Tests:** `test_integration_realistic.py`, `test_dag_integration.py` (single-file vs directory parity), single-file snapshots (verify no-op before/after; do not regenerate).
- **Spirit:** Centralizes the documented pass-ordering invariant in one place.

### Wave D — the crux (highest value, needs care)

**`dev/simplify-render-session-id-from-dag`** (opp 11) — *depends on: `dev/simplify-factor-session-headers`*
- **Does:** Build `uuid_to_render_sid: dict[str,str]` from the `SessionTree` (branch lines → branch sid, agent lines → `parent_session_id` replicating the immediate-parent resolution at renderer.py:3776, trunk lines → trunk sid; trunk messages keep `_render_session_id=None` to fall back to `meta.session_id`). Pass it into `_render_messages`; look up each message's `render_session_id` by uuid. Delete `current_render_session`, the branch-start flip (3789), and the reset (3944). **Crucially: unify header creation and render_session_id assignment under the same map-driven trigger** — do not split them (a branch message tagged to a branch sid with no header would land in the `_reorder_session_template_messages` unmatched tail).
- **Safe alone (with caveat):** Faithful re-derivation of the same grouping from the authoritative `SessionTree`. **NOT byte-identical at non-FULL detail:** if `_filter_by_detail` drops a branch's `first_uuid` message, today's loop variable silently inherits a stale value (a latent bug); the map fixes it. Treat fork-related snapshot diffs at LOW/MINIMAL/USER_ONLY as a latent-bug fix to be reviewed and accepted, not a regression.
- **Tests:** `test_session_export.py`, `test_combined_transcript_link.py`, `test_async_agents.py`, `test_teammates_*`, `test_detail_levels.py`, fork snapshots. Adapt `test_dag_integration.py::TestRenderSessionResetAcrossSessions` (names `current_render_session`; spirit preserved — s2 messages still resolve to `s2`). Verify nested-agent grouping matches immediate-parent resolution exactly.
- **Spirit:** Reads branch/agent grouping from the `SessionTree` (dag.md: graph is authoritative) instead of re-discovering it; DAG ordering and `{trunk}@{uuid}`/`{trunk}#agent-{id}` semantics unchanged.

**`dev/simplify-delete-detail-reindex`** (opp 12) — *depends on: a full ghosting migration of both `_reindex_filtered_context` callers (see Risks)*
- **Does:** Once both callers (`_pair_skill_tool_uses` at renderer.py:3451 and the detail filter at 797) stop deleting from `ctx.messages`, delete `_reindex_filtered_context` and its remap of `session_first_message`/`parent_message_index`/`junction_forward_links`.
- **Safe alone:** Only after both deletions are gone — at that point `message_index == position` holds for the whole pipeline by construction.
- **Tests:** Rewrite `test_skill_pairing.py:565-702` (`TestReindexBranchBackrefs`) — but the replacement MUST exercise the skill-fold-on-a-fork path at FULL detail (the origin of the PR #131 regression), not a non-FULL render, or coverage silently moves to a different path. No new end-to-end test currently combines a Skill invocation with a within-session fork; one must be added.
- **Spirit:** Keeps the PR #131 branch-backlink-under-filtering invariant, verified end-to-end; eliminates the "remember to remap X" fragility class.

**Merge order:** A1–A5 (any order) → B6 → B7 (independent of B6) → C8 → C9 → C10 (independent) → D11 (after B6) → D12 (after the ghosting epic). Monk reviews each PR; `main` merges progressively.

## 5. Spirit-preservation checklist

Every branch must keep these invariants; verification noted per item.

- **DAG ordering is authoritative** (parentUuid→uuid traversal, not timestamps). No branch reintroduces timestamp sorting as primary order. *Verify:* `test_dag.py`, `test_dag_integration.py`, combined snapshots.
- **`_integrate_agent_entries` precedes `build_dag`; subagent `{trunk}#agent-{id}` sessionId rewrite before DAG extraction.** *Verify:* `test_dag.py`, `dag_cycle.jsonl` cycle guard, agents snapshots. (Branches A5, C10 touch this ordering — relocate calls only, never reorder.)
- **Branch pseudo-sessions `{trunk}@{uuid12}`; the same `Branch • <uuid8> • <preview>` string in body header, nav, and fork-point box.** *Verify:* B7 fixtures (assistant-start + slash-command-start branches), fork snapshots, `test_utils.py:606-666` (#129 precedence).
- **Junction forward links require ≥2 navigable branches and survive the detail reindex.** *Verify:* `test_combined_transcript_link.py`, `test_skill_pairing.py:580` (PR #131), D12's new skill-fold-on-fork test.
- **Agent transcripts spliced at anchor, grouped with parent via `render_session_id`; subagent sessions get NO header, relocated under Task/Agent tool_result.** *Verify:* `test_async_agents.py`, `test_teammates_browser.py`. (D11 must replicate immediate-parent resolution exactly.)
- **`message_index == position in ctx.messages`** (RenderingContext.get is positional). Any drop must reindex or ghost. *Verify:* `test_skill_pairing.py` reindex/ghost tests; D11/D12 preserve anchor stability.
- **All five detail levels behave identically** except where D11 surfaces the documented stale-loop-variable latent bug. *Verify:* `test_detail_levels.py` + per-detail snapshot suite (review fork diffs at non-FULL in D11 as intentional).
- **Cache contract:** staleness keys off `sessions.message_count` + `last_timestamp`; mtime + schema-version invalidation; archived-session restore works without live messages. *Verify:* `test_cache.py`, `test_cache_sqlite_integrity.py`; C9 characterization tests pin `message_count`.
- **Title fallback chain `ai_title > summary > preview > Session{id[:8]}`** identical on cache-hit, pagination-fallback, and index paths. *Verify:* `test_ai_title.py`, projects-index nav tests (C8/C9).
- **requestId token dedup is the correct total** on every path. *Verify:* `test_pagination.py`, `test_dag_integration.py` token assertions (A1/C9).
- **Snapshot discipline:** run `--snapshot-update` serially with `-n0` (xdist races truncate `.ambr`). *Verify:* `just ci` before any push.

## 6. Risks & open questions

- **The crux decoupling (D11) is not byte-neutral.** Replacing `current_render_session` with a `SessionTree`-derived map fixes a latent bug (branch messages inheriting a stale session when their `first_uuid` is detail-filtered out), so fork snapshots at LOW/MINIMAL/USER_ONLY *will* change. **Maintainer decision (2026-05-29): ACCEPT the diffs as a correctness fix** — regenerate and review the affected fork snapshots at non-FULL detail when D11 lands.

- **The reindex/ghosting epic (D12) is the hardest and is gated.** The verifier rejected both standalone ghosting opportunities: `detail-ghost-skill-fold` because the level-1 slash body adopts the following assistant turn as a child after `_build_message_hierarchy` (the elision rule then keeps it as a bare card), and `detail-move-template-filter-to-tree` because nav/descendant-counts/backlinks run after the proposed prune point. A *correct* ghosting migration requires teaching `_build_message_hierarchy` (not just `_build_message_tree`) to skip ghosts and graft children to the next surviving ancestor, plus `_mark_messages_with_children` and `_identify_message_pairs` ghost-skips, plus reconciling the `d-{index}` fold cascade. **Maintainer call:** treat `work/refactor-reindex-with-ghosting.md` as a separate, scoped epic with a unified `is_ghosted` flag; D12 (the deletion) only lands after that epic. Do NOT attempt the standalone 10-15 line ghost.

- **Vestigial progress-chain repair — INVESTIGATED & CONFIRMED DELETABLE (2026-05-29).** The hypothesis held up under verification:
  - `progress` is **not** in `SILENT_SKIP_TYPES`; a progress entry with `uuid`+`sessionId` becomes a `PassthroughTranscriptEntry` (present in `messages`), so `_repair_parent_chains` excludes it from `dropped_progress` and early-returns. The repair fires *only* for a progress entry with a `uuid` but **no `sessionId`** (dropped via the else-branch).
  - That case occurs in **0 of 11** real fixtures (every progress entry has a `sessionId`). Even the synthetic `_make_progress_entry` test helper sets `sessionId="s1"`, so the unit tests (`test_single_progress_gap`, `test_chained_progress_gap`, `test_progress_chain_repair_single_file`) already assert **no-op** behavior ("NOT repaired — p2 is in the DAG").
  - `build_dag` already clears dangling parents and promotes such nodes to roots (dag.py:190-220), so even the unreachable case degrades gracefully (at most a suppressible orphan warning).
  - **Empirical proof:** neutering `_repair_parent_chains` to a no-op → **all 145 DAG tests pass**, and the **full unit+snapshot suite passes (1916 passed, 7 skipped)**. Zero behavioral dependence.

  **Action: new Wave A branch `dev/simplify-drop-progress-repair`** — delete `_scan_file_progress`, `_scan_progress_chains`, `_repair_parent_chains` and their two call sites (converter.py:761-762 directory, 1584-1585 single-file); delete `TestScanProgressChains`/`TestRepairParentChains` and the repair-specific integration tests; **keep** the valuable `test_progress_chain_repair_directory` "no orphan warnings" guarantee, renamed to reflect that it's now provided by the Passthrough mechanism, not the repair. **This supersedes opp 5** — with the progress scan gone, `_scan_sidechain_uuids` is the only remaining aux scan, so there is nothing left to "fuse" (opp 5 is dropped, net win is larger: a whole pass + a whole file-re-scan removed rather than two reads merged).

- **C9 reconciliation decisions need sign-off.** Unifying the session scan forces one choice each on (a) the requestId token *gate* (drop no-requestId usage vs count it) and (b) `PassthroughTranscriptEntry` counting (affects `message_count`, the staleness key, and page boundaries). The recommendation is "cache path is canonical," but because both touch the staleness contract and page layout, the characterization tests must land first and the chosen behavior change must be called out in the PR for monk/maintainer review.

- **C8 team_name was deliberately excluded.** `prepare_session_team_names` keys by raw sessionId while the converter coalesces agent-session teamName into the parent via `get_parent_session_id` — not byte-equivalent, and an agent-only session bearing a teamName would be attributed differently. Consolidating team_name is a behavior decision, not a refactor; left out of scope pending a maintainer call on which keying is correct.

## 7. Detail-filtering: the single-axis end-state (added 2026-05-29)

A discussion follow-up that the original analysis didn't surface, and the
most promising structural cleanup of the detail-filter area.

**Observation.** Detail filtering runs on **two axes**: a pre-render pass
(`_filter_by_detail` on raw `TranscriptEntry`, which strips content *items*
within an entry) and a post-render pass (`_content_visible_at` /
`_filter_template_by_detail` on `MessageContent`). The pre-render axis is
**not** a documented performance decision — the only stated rationale for two
passes (application_model.md §2.6) is that some content is identifiable only
after factory dispatch. Functionally, *everything* the pre-render pass does is
expressible post-render, because the factory already splits a single entry's
content items into separate messages. The pre-render axis earns its keep today
only by keeping dropped content **out of the message index**, which sidesteps
`_reindex_filtered_context`. So it's "complexity here to avoid complexity
there," not a necessity.

**End-state.** One filtering axis (post-render) + ghosting (dropped messages
keep their index slot) + one per-class visibility predicate ⇒ delete *both*
`_filter_by_detail` (pre-render) and `_reindex_filtered_context`. This is the
[ghosting epic](refactor-reindex-with-ghosting.md) — the gate for D12 — now
with a clear motivation. The single-axis collapse of the pre-render filter is a
**new addition to that epic's scope** (it previously listed `_filter_by_detail`
as out of scope).

**New branch — `wf/simplify/detail-visibility-method` (enabler; behavior-neutral).**
Builds on #176's `detail_visibility` ClassVar migration. Introduce a polymorphic
per-class visibility predicate (`MessageContent.visible_at(detail)`, default =
read the `detail_visibility` ClassVar via the monotone-down ordering), migrate
the built-ins currently in the four `_*_EXCLUDE_CLASSES` tuples onto ClassVar
declarations (all are monotone-down expressible since the tuples are
cumulative), rewrite `_content_visible_at` to delegate to the predicate, and
**delete the four exclude tuples**. Preserves the `detail_visibility` plugin
contract (plugins.md §6). `_LOW_KEEP_TOOLS` (tool-name allowlist) stays as-is —
orthogonal, out of scope. This single predicate is the foundation the eventual
ghosting/single-axis work routes the post-render path through. **Assigned:
alice (implement) under carol's supervision; alice also amends
`work/refactor-reindex-with-ghosting.md` to record the single-axis end-state.**
