"""DAG-based message ordering for Claude Code transcripts.

Replaces timestamp-based ordering with parentUuid → uuid graph traversal.
Works at the TranscriptEntry level (before factory/rendering).

See dev-docs/dag.md for the full architecture spec.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from .models import (
    TranscriptEntry,
    SummaryTranscriptEntry,
    QueueOperationTranscriptEntry,
    ToolUseContent,
    UserTranscriptEntry,
    AssistantTranscriptEntry,
    PassthroughTranscriptEntry,
    SystemTranscriptEntry,
)

# Tool names whose ``tool_use`` block spawns a subagent thread. Used by
# ``_collect_agent_anchors`` to keep nested-Agent assistant entries from
# being swept up as silent dead-end skips.
_SPAWN_TOOL_NAMES: frozenset[str] = frozenset({"Task", "Agent"})

logger = logging.getLogger(__name__)


# =============================================================================
# Data Structures
# =============================================================================


@dataclass
class MessageNode:
    """A deduplicated message in the DAG."""

    uuid: str
    parent_uuid: Optional[str]
    session_id: str
    timestamp: str
    entry: TranscriptEntry
    children_uuids: list[str] = field(default_factory=lambda: [])


@dataclass
class SessionDAGLine:
    """A session's ordered chain of unique messages."""

    session_id: str
    uuids: list[str]  # Ordered by parent→child chain traversal
    first_timestamp: str
    parent_session_id: Optional[str] = None
    attachment_uuid: Optional[str] = None  # UUID in parent where this attaches
    is_branch: bool = False  # True for within-session fork branches
    original_session_id: Optional[str] = None  # Original session_id before fork split
    is_sidechain: bool = False  # True for agent transcript sessions


@dataclass
class JunctionPoint:
    """A message where other sessions fork or continue."""

    uuid: str
    session_id: str  # The session this message belongs to
    target_sessions: list[str] = field(default_factory=lambda: [])


@dataclass
class SessionTree:
    """The complete session hierarchy for a project."""

    nodes: dict[str, MessageNode]
    sessions: dict[str, SessionDAGLine]
    roots: list[str]  # Root session IDs (no parent session)
    junction_points: dict[str, JunctionPoint]


# =============================================================================
# Step 1: Load and Index
# =============================================================================


def build_message_index(
    entries: list[TranscriptEntry],
) -> dict[str, MessageNode]:
    """Build a deduplicated message index from transcript entries.

    Skips SummaryTranscriptEntry (no uuid/sessionId) and
    QueueOperationTranscriptEntry (no uuid). For duplicate uuids,
    keeps the entry from the earliest session (by first entry timestamp).
    """
    # First pass: determine earliest timestamp per session
    session_first_ts: dict[str, str] = {}
    for entry in entries:
        if isinstance(entry, (SummaryTranscriptEntry, QueueOperationTranscriptEntry)):
            continue
        sid = entry.sessionId
        ts = entry.timestamp
        if sid not in session_first_ts or ts < session_first_ts[sid]:
            session_first_ts[sid] = ts

    # Second pass: build nodes, deduplicating by uuid (earliest session wins)
    nodes: dict[str, MessageNode] = {}
    for entry in entries:
        if isinstance(entry, (SummaryTranscriptEntry, QueueOperationTranscriptEntry)):
            continue
        uuid = entry.uuid
        sid = entry.sessionId
        if uuid in nodes:
            existing = nodes[uuid]
            existing_session_ts = session_first_ts.get(existing.session_id, "")
            new_session_ts = session_first_ts.get(sid, "")
            if new_session_ts < existing_session_ts:
                # Replace with entry from earlier session
                nodes[uuid] = MessageNode(
                    uuid=uuid,
                    parent_uuid=entry.parentUuid,
                    session_id=sid,
                    timestamp=entry.timestamp,
                    entry=entry,
                )
        else:
            nodes[uuid] = MessageNode(
                uuid=uuid,
                parent_uuid=entry.parentUuid,
                session_id=sid,
                timestamp=entry.timestamp,
                entry=entry,
            )

    return nodes


# =============================================================================
# Step 2: Build DAG (parent→children links)
# =============================================================================


def build_dag(
    nodes: dict[str, MessageNode],
    sidechain_uuids: set[str] | None = None,
) -> None:
    """Populate children_uuids on each node. Mutates nodes in place.

    Warns about orphan nodes (parentUuid points outside loaded data)
    and validates acyclicity. Parents known to be in unloaded sidechain
    data (e.g. aprompt_suggestion agents) are silently promoted to root
    without warning.
    """
    _sidechain_uuids = sidechain_uuids or set()

    # Clear existing children
    for node in nodes.values():
        node.children_uuids = []

    # Build parent→children links
    for node in nodes.values():
        if node.parent_uuid is not None:
            parent = nodes.get(node.parent_uuid)
            if parent is not None:
                parent.children_uuids.append(node.uuid)
            else:
                if node.parent_uuid not in _sidechain_uuids:
                    logger.warning(
                        "Orphan node %s: parentUuid %s not found in loaded"
                        " data (promoting to root)",
                        node.uuid,
                        node.parent_uuid,
                    )
                # Clear the dangling parent so this node becomes a root
                # and can participate in DAG walks
                node.parent_uuid = None

    # Validate: no cycles (walk parent chain for each node)
    for node in nodes.values():
        visited: set[str] = set()
        current: Optional[str] = node.uuid
        while current is not None:
            if current in visited:
                logger.warning("Cycle detected in parent chain at uuid %s", current)
                nodes[current].parent_uuid = None
                break
            visited.add(current)
            parent = nodes.get(current)
            if parent is None:
                break
            current = parent.parent_uuid


# =============================================================================
# Step 3: Extract Session DAG-lines
# =============================================================================


def _collect_descendants(
    uuid: str,
    session_uuids: set[str],
    nodes: dict[str, MessageNode],
    result: set[str],
) -> None:
    """Recursively collect a node and all its same-session descendants."""
    if uuid in result:
        return
    result.add(uuid)
    node = nodes.get(uuid)
    if node is None:
        return
    for child in node.children_uuids:
        if child in session_uuids:
            _collect_descendants(child, session_uuids, nodes, result)


def _collect_agent_anchors(
    uuid: str,
    session_uuids: set[str],
    nodes: dict[str, MessageNode],
    max_depth: int = 20,
) -> list[str]:
    """Find spawn-related descendants worth lifting back into the chain.

    Two kinds of "anchors" are surfaced:

    1. ``UserTranscriptEntry`` with an ``agentId`` set — the trunk
       tool_results that anchor subagent sessions: the subagent's
       sidechain entries list this UUID as their ``parentUuid``, and
       the session tree attaches the subagent DAG-line here.
    2. ``AssistantTranscriptEntry`` whose ``message.content`` carries a
       ``Task`` or ``Agent`` tool_use block — i.e. a spawning
       assistant in a nested-Agent chain. Without this, a fork point
       higher up classifies the whole nested-Agent subtree as dead-end
       and its inner spawning assistants get dropped, hiding every
       nested Agent invocation from the rendered transcript (concrete
       repro: the wave-1/wave-2 carol agents in the experiments-
       worktrees fixture).

    When the stitch logic classifies a sibling subtree as dead-end,
    these anchors would otherwise be dropped; surfacing them keeps
    both the subagent attachments AND the spawning tool_use cards
    reachable.
    """
    anchors: list[str] = []
    stack: list[tuple[str, int]] = [(uuid, 0)]
    seen: set[str] = set()
    while stack:
        current, depth = stack.pop()
        if current in seen or current not in session_uuids:
            continue
        seen.add(current)
        if depth >= max_depth:
            continue
        entry = nodes[current].entry
        if isinstance(entry, UserTranscriptEntry) and entry.agentId:
            anchors.append(current)
        elif isinstance(entry, AssistantTranscriptEntry) and any(
            isinstance(item, ToolUseContent) and item.name in _SPAWN_TOOL_NAMES
            for item in entry.message.content
        ):
            anchors.append(current)
        for c in nodes[current].children_uuids:
            stack.append((c, depth + 1))
    return anchors


def _is_subtree_dead_end(
    uuid: str,
    session_uuids: set[str],
    nodes: dict[str, MessageNode],
    max_depth: int = 20,
) -> bool:
    """Check if a node's subtree eventually terminates (no continuation).

    A subtree is a dead end if every leaf within the session has no
    same-session children.  Walks depth-first with a depth limit to
    avoid runaway traversals.
    """
    stack: list[tuple[str, int]] = [(uuid, 0)]
    while stack:
        current, depth = stack.pop()
        children = [c for c in nodes[current].children_uuids if c in session_uuids]
        if not children:
            continue  # Leaf — dead end, keep checking siblings
        if depth >= max_depth:
            return False  # Too deep to tell — assume not dead end
        for c in children:
            stack.append((c, depth + 1))
    return True


def _is_structural_subtree(
    uuid: str,
    session_uuids: set[str],
    nodes: dict[str, MessageNode],
    max_depth: int = 20,
) -> bool:
    """Check if the subtree below `uuid` contains only structural entries.

    A subtree is 'structural' if the root's descendants (within the session)
    contain no UserTranscriptEntry or AssistantTranscriptEntry — only
    passthrough nodes (attachments, permission-mode), system-info hook
    summaries, etc.  Used to detect side-branches that look live at the DAG
    level but carry no conversational content (e.g. a user(tool_result)
    followed by just a hook_success attachment).

    The root itself is not inspected — only its descendants — because this
    check is used to decide whether a child of a fork point represents
    continuing conversation.
    """
    stack: list[tuple[str, int]] = [(c, 1) for c in nodes[uuid].children_uuids]
    seen: set[str] = set()
    while stack:
        current, depth = stack.pop()
        if current in seen or current not in session_uuids:
            continue
        seen.add(current)
        entry = nodes[current].entry
        if isinstance(entry, (UserTranscriptEntry, AssistantTranscriptEntry)):
            return False  # Found conversational content
        if depth >= max_depth:
            return False  # Too deep to tell — be conservative
        for c in nodes[current].children_uuids:
            stack.append((c, depth + 1))
    return True


# System subtypes that produce parentless entries by design (fresh chains
# after `/compact`, orphan `/memory` or `/config` invocations at session
# start). Multi-root sessions built from only these are expected, not
# noteworthy.
_EXPECTED_ROOT_SYSTEM_SUBTYPES = frozenset({"compact_boundary", "local_command"})


def _is_expected_root_type(entry: TranscriptEntry) -> bool:
    """Whether a multi-root entry is one of Claude Code's expected patterns."""
    if isinstance(entry, SystemTranscriptEntry):
        return entry.subtype in _EXPECTED_ROOT_SYSTEM_SUBTYPES
    return False


def _stitch_tool_results(
    children: list[str],
    session_uuids: set[str],
    nodes: dict[str, MessageNode],
) -> Optional[list[str]]:
    """Detect and stitch tool-result side-branches into a linear chain.

    When the assistant makes multiple tool calls in one turn, the JSONL
    records both the next tool_use and the tool_result as children of the
    current tool_use entry, creating a false fork.  Two variants:

    Variant 1 — User child's subtree is structural (no conversation):
        A(tool_use) → U(tool_result)          [structural side-branch]
                         → attachment(hook)
                    → A(next tool_use) → ...  [main chain continues]

    Variant 2 — User child continues, Assistant subtree dead-ends:
        A(tool_use) → U(tool_result) → A(response) → ...  [main chain]
                    → A(tool_use) → ... → dead ends       [progress artifact]

    Returns a stitched ordering placing dead-end children first, then
    the single continuation child.  Returns None if the pattern doesn't
    match.  Callers should treat `result[:-1]` as dead-end nodes whose
    subtree descendants are skipped, and `result[-1]` as the continuation.
    """
    # Separate into user (tool_result) and assistant (continuation) children
    user_children = [
        c for c in children if isinstance(nodes[c].entry, UserTranscriptEntry)
    ]
    assistant_children = [
        c for c in children if isinstance(nodes[c].entry, AssistantTranscriptEntry)
    ]

    if not user_children or not assistant_children:
        return None  # Not the tool_result pattern

    # Variant 1: user children carry only structural content (attachments,
    # hook summaries), the assistant sibling is the real continuation.
    # The earlier "no immediate same-session child" check missed cases
    # where the tool_result has a hook_success attachment leaf.
    user_all_structural = all(
        _is_structural_subtree(uc, session_uuids, nodes) for uc in user_children
    )

    if user_all_structural:
        if len(assistant_children) != 1:
            return None
        user_children.sort(key=lambda c: nodes[c].timestamp)
        return user_children + assistant_children

    # Variant 2: assistant subtrees are dead ends,
    # exactly one user child continues
    user_with_cont = [
        uc
        for uc in user_children
        if any(c in session_uuids for c in nodes[uc].children_uuids)
    ]
    if len(user_with_cont) != 1:
        return None  # Ambiguous — multiple user continuations

    # Verify all assistant children's subtrees are dead ends
    for ac in assistant_children:
        if not _is_subtree_dead_end(ac, session_uuids, nodes):
            return None

    # Verify remaining user children (without continuation) are dead ends
    user_dead = [uc for uc in user_children if uc not in user_with_cont]
    for uc in user_dead:
        if not _is_subtree_dead_end(uc, session_uuids, nodes):
            return None

    # Agent-anchor preservation: parallel `Task` tool_uses emit sibling
    # tool_result anchors whose parent assistants are each other's dead-end
    # subtree. Extracting anchors ensures their subagent sessions stay
    # attached even when the continuation runs through an outer sibling.
    # Also surfaces nested-spawn assistants so their Agent tool_use cards
    # don't disappear from the trunk view.
    extracted_anchors: list[str] = []
    for ac in assistant_children:
        extracted_anchors.extend(_collect_agent_anchors(ac, session_uuids, nodes))

    # Stitch: dead-end children first, then the continuing user child.
    # Dedup: the assistant_children already cover their own roots; the
    # anchor collector also returns those when they carry spawn tool_use
    # blocks, so a plain concatenation would emit them twice.
    seen: set[str] = set()
    dead_ends: list[str] = []
    for uuid in user_dead + assistant_children + extracted_anchors:
        if uuid in seen:
            continue
        seen.add(uuid)
        dead_ends.append(uuid)
    dead_ends.sort(key=lambda c: nodes[c].timestamp)
    return dead_ends + user_with_cont


def _walk_session_with_forks(
    root: MessageNode,
    session_id: str,
    session_uuids: set[str],
    nodes: dict[str, MessageNode],
) -> tuple[list[SessionDAGLine], set[str]]:
    """Walk a session's DAG from root, splitting into separate DAG-lines at fork points.

    Uses a queue-based approach to handle nested forks:
    1. Start with (root_uuid, session_id, None) in the queue
    2. Walk chain following single same-session children
    3. On fork (multiple same-session children): stop chain at fork point,
       push each child as a new branch
    4. Update MessageNode.session_id for branch nodes

    Returns:
        Tuple of (DAG-line list, set of UUIDs intentionally skipped as
        compaction replays).
    """
    # Queue entries: (start_uuid, dag_line_id, parent_dag_line_id)
    queue: list[tuple[str, str, Optional[str]]] = [(root.uuid, session_id, None)]
    result: list[SessionDAGLine] = []
    skipped: set[str] = set()  # Compaction replay UUIDs

    while queue:
        start_uuid, line_id, parent_line_id = queue.pop(0)
        chain: list[str] = []
        current: Optional[MessageNode] = nodes[start_uuid]
        is_branch = line_id != session_id

        while current is not None:
            chain.append(current.uuid)
            # Update session_id for branch nodes (needed for build_session_tree)
            if is_branch:
                current.session_id = line_id

            # Find children in the original session
            same_session_children = [
                c for c in current.children_uuids if c in session_uuids
            ]
            if len(same_session_children) == 0:
                current = None
            elif len(same_session_children) == 1:
                current = nodes[same_session_children[0]]
            else:
                # Multiple same-session children. Distinguish real forks
                # from artifacts (see dev-docs/dag.md caveats).
                same_session_children.sort(key=lambda c: nodes[c].timestamp)

                # Collapse passthrough side-branches. A child is
                # "structural" when it is a PassthroughTranscriptEntry
                # whose subtree has no user/assistant descendants — e.g. a
                # `progress` entry, a `hook_success` attachment, or a chain
                # of them. When at most one non-structural child remains,
                # the chain continues through that child (or terminates if
                # none remain); the structural children are stitched in
                # chronologically as dead-end side entries.
                #
                # This catches both the all-passthrough case (e.g. two
                # hook attachments on the same parent) and the common
                # mixed case (a `<progress>` sibling of a real user
                # message), preventing spurious 1-branch forks.
                structural_kids = [
                    c
                    for c in same_session_children
                    if isinstance(nodes[c].entry, PassthroughTranscriptEntry)
                    and _is_structural_subtree(c, session_uuids, nodes)
                ]
                non_structural = [
                    c for c in same_session_children if c not in structural_kids
                ]
                if structural_kids and len(non_structural) <= 1:
                    for pk in sorted(structural_kids, key=lambda c: nodes[c].timestamp):
                        if is_branch:
                            nodes[pk].session_id = line_id
                        _collect_descendants(pk, session_uuids, nodes, skipped)
                        chain.append(pk)
                    current = nodes[non_structural[0]] if non_structural else None
                    continue

                stitched = _stitch_tool_results(
                    same_session_children, session_uuids, nodes
                )
                if stitched is not None:
                    # Tool-result side-branches were stitched into the
                    # chain. The last element is the continuation; all
                    # others are dead-end nodes whose subtree descendants
                    # must be skipped.
                    for su in stitched[:-1]:
                        if is_branch:
                            nodes[su].session_id = line_id
                        _collect_descendants(su, session_uuids, nodes, skipped)
                    chain.extend(stitched[:-1])
                    current = nodes[stitched[-1]]
                else:
                    unique_timestamps = {
                        nodes[c].timestamp for c in same_session_children
                    }
                    if len(unique_timestamps) == 1:
                        # Same timestamp = compaction replay: follow only
                        # the first child (original chain), skip replays
                        # and all their descendants.
                        current = nodes[same_session_children[0]]
                        for sc in same_session_children[1:]:
                            _collect_descendants(sc, session_uuids, nodes, skipped)
                    else:
                        # Different timestamps = real fork (rewind).
                        # Stop chain here, push each child as a branch.
                        for child_uuid in same_session_children:
                            branch_id = f"{line_id}@{child_uuid[:12]}"
                            queue.append((child_uuid, branch_id, line_id))
                        current = None

        if chain:
            first_ts = nodes[chain[0]].timestamp
            dag_line = SessionDAGLine(
                session_id=line_id,
                uuids=chain,
                first_timestamp=first_ts,
                is_branch=is_branch,
                original_session_id=session_id if is_branch else None,
            )
            # Set parent/attachment for branches
            if is_branch and parent_line_id is not None:
                parent_uuid = nodes[chain[0]].parent_uuid
                dag_line.parent_session_id = parent_line_id
                dag_line.attachment_uuid = parent_uuid
            result.append(dag_line)

    return result, skipped


def extract_session_dag_lines(
    nodes: dict[str, MessageNode],
) -> dict[str, SessionDAGLine]:
    """Extract per-session ordered chains from the DAG.

    For each session, finds the root node (parent_uuid is null or points
    to a different session), then walks forward via children_uuids filtering
    to same-session children.

    Within-session forks (multiple same-session children) produce additional
    DAG-lines with synthetic IDs (e.g., "s1@child_uuid12").
    Falls back to timestamp sort only when no root is found.
    """
    # Group nodes by session
    session_nodes: dict[str, list[MessageNode]] = {}
    for node in nodes.values():
        session_nodes.setdefault(node.session_id, []).append(node)

    sessions: dict[str, SessionDAGLine] = {}
    for session_id, snodes in session_nodes.items():
        session_uuids = {n.uuid for n in snodes}

        # Find root(s): nodes whose parent_uuid is null or outside this session
        roots = [
            n
            for n in snodes
            if n.parent_uuid is None or n.parent_uuid not in session_uuids
        ]

        if not roots:
            logger.warning(
                "Session %s: no root found, falling back to timestamp sort",
                session_id,
            )
            sorted_nodes = sorted(snodes, key=lambda n: n.timestamp)
            sessions[session_id] = SessionDAGLine(
                session_id=session_id,
                uuids=[n.uuid for n in sorted_nodes],
                first_timestamp=sorted_nodes[0].timestamp,
            )
            continue

        # Sort roots by timestamp (earliest first = primary root)
        roots.sort(key=lambda n: n.timestamp)
        if len(roots) > 1:
            # Roots that arise from expected Claude Code mechanisms —
            # `/compact` writes a compact_boundary system entry with no
            # parentUuid; early `local_command` entries like `/memory`
            # sometimes land as orphans too. Warn only when an unexpected
            # root shows up (e.g. an orphan user/assistant that hints at a
            # missing parent); otherwise log at debug level.
            unexpected = [n for n in roots if not _is_expected_root_type(n.entry)]
            if unexpected:
                logger.warning(
                    "Session %s: %d roots found (%d unexpected), "
                    "walking all from earliest (%s)",
                    session_id,
                    len(roots),
                    len(unexpected),
                    roots[0].uuid,
                )
            else:
                logger.debug(
                    "Session %s: %d expected roots (compact_boundary / "
                    "local_command), walking all from earliest (%s)",
                    session_id,
                    len(roots),
                    roots[0].uuid,
                )

        # Walk from ALL roots to maximize coverage (orphan-promoted roots
        # create disconnected subtrees that must each be walked)
        dag_lines: list[SessionDAGLine] = []
        walked_uuids: set[str] = set()
        skipped_uuids: set[str] = set()
        for root in roots:
            if root.uuid in walked_uuids:
                continue
            root_lines, root_skipped = _walk_session_with_forks(
                root, session_id, session_uuids, nodes
            )
            for dl in root_lines:
                walked_uuids.update(dl.uuids)
            skipped_uuids.update(root_skipped)
            dag_lines.extend(root_lines)

        # Check coverage: walked + intentionally skipped (compaction replays)
        covered = len(walked_uuids | skipped_uuids)
        if covered < len(snodes):
            logger.warning(
                "Session %s: DAG walk covers %d of %d nodes, "
                "falling back to timestamp sort",
                session_id,
                covered,
                len(snodes),
            )
            sorted_nodes = sorted(snodes, key=lambda n: n.timestamp)
            sessions[session_id] = SessionDAGLine(
                session_id=session_id,
                uuids=[n.uuid for n in sorted_nodes],
                first_timestamp=sorted_nodes[0].timestamp,
            )
        else:
            # Merge non-branch DAG-lines that share the same session_id
            # (happens when multiple roots exist due to orphan promotion)
            trunk_lines = [dl for dl in dag_lines if dl.session_id == session_id]
            branch_lines = [dl for dl in dag_lines if dl.session_id != session_id]
            if trunk_lines:
                # Merge all trunk lines into one, ordered by first_timestamp
                trunk_lines.sort(key=lambda dl: dl.first_timestamp)
                merged_uuids: list[str] = []
                for tl in trunk_lines:
                    merged_uuids.extend(tl.uuids)
                sessions[session_id] = SessionDAGLine(
                    session_id=session_id,
                    uuids=merged_uuids,
                    first_timestamp=trunk_lines[0].first_timestamp,
                )
            for dag_line in branch_lines:
                sessions[dag_line.session_id] = dag_line

    return sessions


# =============================================================================
# Step 4: Build Session Tree
# =============================================================================


def build_session_tree(
    nodes: dict[str, MessageNode],
    sessions: dict[str, SessionDAGLine],
) -> SessionTree:
    """Build the session hierarchy and identify junction points.

    For each session's DAG-line, the first message's parent_uuid determines
    the parent session:
    - null → root session
    - points to node in different session → child of that session
    """
    roots: list[str] = []
    junction_points: dict[str, JunctionPoint] = {}

    for session_id, dag_line in sessions.items():
        if not dag_line.uuids:
            roots.append(session_id)
            continue

        first_uuid = dag_line.uuids[0]
        first_node = nodes[first_uuid]
        parent_uuid = first_node.parent_uuid

        if parent_uuid is None or parent_uuid not in nodes:
            # Root session (or orphan parent)
            roots.append(session_id)
            dag_line.parent_session_id = None
            dag_line.attachment_uuid = None
        else:
            parent_node = nodes[parent_uuid]
            if parent_node.session_id == session_id:
                # Parent is in same session - this is a root
                roots.append(session_id)
                dag_line.parent_session_id = None
                dag_line.attachment_uuid = None
            else:
                # Child session: attaches to parent session at parent_uuid
                dag_line.parent_session_id = parent_node.session_id
                dag_line.attachment_uuid = parent_uuid

                # Record junction point
                if parent_uuid not in junction_points:
                    junction_points[parent_uuid] = JunctionPoint(
                        uuid=parent_uuid,
                        session_id=parent_node.session_id,
                    )
                junction_points[parent_uuid].target_sessions.append(session_id)

    # Order roots chronologically
    roots.sort(key=lambda sid: sessions[sid].first_timestamp)

    # Order junction point target_sessions chronologically
    for jp in junction_points.values():
        jp.target_sessions.sort(key=lambda sid: sessions[sid].first_timestamp)

    return SessionTree(
        nodes=nodes,
        sessions=sessions,
        roots=roots,
        junction_points=junction_points,
    )


# =============================================================================
# Step 5: Ordered Traversal
# =============================================================================


def traverse_session_tree(tree: SessionTree) -> list[TranscriptEntry]:
    """Depth-first traversal of session tree producing rendering order.

    For each session: yields its DAG-line's entries in chain order.
    Children are visited in chronological order (by first_timestamp).
    """
    result: list[TranscriptEntry] = []
    visited_sessions: set[str] = set()

    def _visit_session(session_id: str) -> None:
        if session_id in visited_sessions:
            return
        visited_sessions.add(session_id)

        dag_line = tree.sessions.get(session_id)
        if dag_line is None:
            return

        # Build map: attachment_uuid → [child session IDs] for this session
        children_at: dict[str, list[str]] = {}
        for sid, sline in tree.sessions.items():
            if sline.parent_session_id == session_id and sline.attachment_uuid:
                children_at.setdefault(sline.attachment_uuid, []).append(sid)
        for child_sids in children_at.values():
            child_sids.sort(key=lambda sid: tree.sessions[sid].first_timestamp)

        # Emit entries, visiting child sessions at junction points
        for uuid in dag_line.uuids:
            node = tree.nodes[uuid]
            result.append(node.entry)
            # After emitting this message, visit any child sessions
            # that attach here (in chronological order)
            if uuid in children_at:
                for child_sid in children_at[uuid]:
                    _visit_session(child_sid)

    # Visit root sessions in chronological order
    for root_sid in tree.roots:
        _visit_session(root_sid)

    return result


# =============================================================================
# Convenience: Full Pipeline
# =============================================================================


def build_dag_from_entries(
    entries: list[TranscriptEntry],
    sidechain_uuids: set[str] | None = None,
) -> SessionTree:
    """Build a complete SessionTree from raw transcript entries.

    Convenience function that runs Steps 1-4 in sequence.
    ``sidechain_uuids`` suppresses orphan warnings for parents known
    to be in unloaded sidechain data (e.g. aprompt_suggestion agents
    that are never referenced via agentId in the main session).
    """
    nodes = build_message_index(entries)
    build_dag(nodes, sidechain_uuids=sidechain_uuids)
    sessions = extract_session_dag_lines(nodes)
    return build_session_tree(nodes, sessions)
