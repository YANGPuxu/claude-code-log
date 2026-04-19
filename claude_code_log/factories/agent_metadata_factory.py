"""Parser for the trailing metadata block on Task/agent tool_results.

Teammate-spawned agents (and async task agents — issues #79, #90, #91) embed
a structured metadata block at the end of their response, e.g.:

    agentId: a4ca7529859c158c2 (use SendMessage with to: '...' to continue this agent)
    worktreePath: /.../worktrees/agent-a4ca7529
    worktreeBranch: worktree-agent-a4ca7529
    <usage>total_tokens: 48421
    tool_uses: 24
    duration_ms: 802753</usage>

In practice the block may live in its own text content item (separate from
the markdown response) or appended to the end of a single text item. This
module extracts the structured fields and returns the body stripped of the
tail so the renderer can show a clean response.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

from ..models import AgentResultMetadata


# `[^\S\r\n]*` matches horizontal whitespace only (not the leading newline),
# so each ^-anchored line is captured from its own start. The path/branch
# values use `(.+?)` + trailing whitespace trim so paths containing spaces
# survive intact (coderabbit #117).
_AGENT_ID_RE = re.compile(r"^[^\S\r\n]*agentId:\s*(\S+)", re.MULTILINE)
_WORKTREE_PATH_RE = re.compile(
    r"^[^\S\r\n]*worktreePath:[^\S\r\n]*(.+?)[^\S\r\n]*$",
    re.MULTILINE,
)
_WORKTREE_BRANCH_RE = re.compile(
    r"^[^\S\r\n]*worktreeBranch:[^\S\r\n]*(.+?)[^\S\r\n]*$",
    re.MULTILINE,
)
_USAGE_RE = re.compile(r"<usage>(.*?)</usage>", re.DOTALL)
_TOTAL_TOKENS_RE = re.compile(r"total_tokens:\s*(\d+)")
_TOOL_USES_RE = re.compile(r"tool_uses:\s*(\d+)")
_DURATION_MS_RE = re.compile(r"duration_ms:\s*(\d+)")


def parse_agent_result_metadata(
    text: str,
) -> Tuple[str, Optional[AgentResultMetadata]]:
    """Extract AgentResultMetadata from the tail of *text*.

    Returns a ``(body, metadata)`` tuple. ``body`` is *text* with the
    metadata block stripped; ``metadata`` is ``None`` when no recognizable
    metadata is present (so the caller can render the original text as-is).
    """
    if not text:
        return text, None

    # Anchor on the *last* line starting with `agentId:` so body text that
    # discusses `agentId:` (e.g. an agent logging its own id mid-response)
    # isn't mistaken for the metadata tail (coderabbit #117).
    all_agent_id_matches = list(_AGENT_ID_RE.finditer(text))
    if all_agent_id_matches:
        tail_start = all_agent_id_matches[-1].start()
    else:
        # The `<usage>` block may appear alone in older transcripts — treat
        # it as metadata too so its body isn't clobbered into the response.
        usage_match = _USAGE_RE.search(text)
        if usage_match is None:
            return text, None
        tail_start = usage_match.start()

    body = text[:tail_start].rstrip()
    tail = text[tail_start:]

    agent_id = _first_group(_AGENT_ID_RE, tail)
    worktree_path = _first_group(_WORKTREE_PATH_RE, tail)
    worktree_branch = _first_group(_WORKTREE_BRANCH_RE, tail)

    total_tokens: Optional[int] = None
    tool_uses: Optional[int] = None
    duration_ms: Optional[int] = None
    usage_match = _USAGE_RE.search(tail)
    if usage_match is not None:
        usage_body = usage_match.group(1)
        total_tokens = _first_int(_TOTAL_TOKENS_RE, usage_body)
        tool_uses = _first_int(_TOOL_USES_RE, usage_body)
        duration_ms = _first_int(_DURATION_MS_RE, usage_body)

    metadata = AgentResultMetadata(
        agent_id=agent_id,
        worktree_path=worktree_path,
        worktree_branch=worktree_branch,
        total_tokens=total_tokens,
        tool_uses=tool_uses,
        duration_ms=duration_ms,
    )
    return body, metadata


def _first_group(regex: re.Pattern[str], text: str) -> Optional[str]:
    match = regex.search(text)
    return match.group(1) if match else None


def _first_int(regex: re.Pattern[str], text: str) -> Optional[int]:
    match = regex.search(text)
    return int(match.group(1)) if match else None
