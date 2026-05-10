"""Regression tests for issue #139: lone-surrogate UnicodeEncodeError.

Background
----------
Claude Code logs file contents using `surrogateescape`, which encodes
non-UTF-8 bytes (e.g. Latin-1 ``0xB2``) as lone surrogate code points
like ``U+DCB2``. JSONL on disk represents these as the literal escape
``\\udcb2``. ``json.loads`` rebuilds the lone surrogate; strict
UTF-8 encoding (the default for ``Path.write_text``) crashes with
``UnicodeEncodeError`` when one of these flows into the rendered HTML.

The fix adds ``errors="replace"`` to the converter's ``write_text``
(and one paired ``read_text``) call sites so lone surrogates collapse
to ``U+FFFD`` rather than crashing the whole run.
"""

import json
from pathlib import Path

import pytest

from claude_code_log.converter import convert_jsonl_to_html


# Lone low surrogate U+DCB2 — what surrogateescape uses for byte 0xB2.
LONE_SURROGATE = "\udcb2"


def _make_jsonl_with_surrogate(jsonl_path: Path) -> None:
    """Write a minimal JSONL transcript whose user-text content carries a
    lone surrogate. The shape mirrors what Claude Code emits when a tool
    returns binary file content via ``surrogateescape`` decoding."""
    user_entry = {
        "parentUuid": None,
        "isSidechain": False,
        "userType": "external",
        "cwd": "/tmp",
        "sessionId": "11111111-1111-1111-1111-111111111111",
        "version": "2.1.0",
        "type": "user",
        "uuid": "22222222-2222-2222-2222-222222222222",
        "timestamp": "2026-05-07T10:00:00.000Z",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    # Embed the lone surrogate inside otherwise-valid text.
                    "text": f"non-utf8 byte placeholder: {LONE_SURROGATE} (issue #139)",
                }
            ],
        },
    }
    # ``ensure_ascii=False`` keeps the literal surrogate in memory; the
    # final on-disk JSONL escapes it as ``\udcb2`` either way (Python's
    # JSON encoder permits unpaired surrogates by default).
    jsonl_path.write_text(json.dumps(user_entry) + "\n", encoding="utf-8")


class TestSurrogateEncoding:
    def test_convert_does_not_crash_on_lone_surrogate(self, tmp_path: Path):
        """Issue #139's minimum bar: end-to-end conversion of a transcript
        carrying a lone surrogate must not raise UnicodeEncodeError."""
        jsonl_path = tmp_path / "session.jsonl"
        _make_jsonl_with_surrogate(jsonl_path)

        # Should complete without raising.
        output = convert_jsonl_to_html(jsonl_path, silent=True)
        assert output.exists(), "converter returned a path that wasn't written"

    def test_output_html_is_valid_utf8(self, tmp_path: Path):
        """Stronger guarantee: the bytes on disk decode as strict UTF-8.
        ``errors="replace"`` collapses any surviving lone surrogate to
        ``U+FFFD``, which is itself valid UTF-8, so a strict re-read must
        succeed regardless of which intermediate layer (mistune,
        html.escape, …) replaced it."""
        jsonl_path = tmp_path / "session.jsonl"
        _make_jsonl_with_surrogate(jsonl_path)

        output = convert_jsonl_to_html(jsonl_path, silent=True)

        # Strict decode — would raise UnicodeDecodeError if the file still
        # held a lone surrogate written via the buggy strict path.
        html = output.read_bytes().decode("utf-8")
        assert "issue #139" in html, "marker text from the fixture missing"
        # The lone surrogate must not survive into the output bytes; the
        # specific replacement character ("?", U+FFFD, or HTML entity)
        # depends on which layer caught it first, but the surrogate
        # itself is the canary.
        assert LONE_SURROGATE not in html

    def test_paginated_read_rewrite_does_not_crash(self, tmp_path: Path):
        """Exercise `_enable_next_link_on_previous_page`'s read+write seam.

        Pagination only fires when the input is a *directory*, the format
        is HTML, no date filter is set, and `total_message_count >
        page_size`. We force it by passing `page_size=1` against a
        directory-mode input with two messages, so page 2 generation
        triggers a read of page 1 (`read_text(errors="replace")`) and a
        rewrite of it (`write_text(errors="replace")`).

        Pre-fix, that read would raise `UnicodeDecodeError` on a page
        whose previous run had baked in a lone surrogate; the read-side
        fix lets older corrupt pages still rewrite cleanly."""
        # Pagination splits by *session*, never within a session
        # (`_assign_sessions_to_pages`). Two distinct sessions with
        # `page_size=1` → page 1 holds the surrogate-bearing session,
        # page 2 holds the plain one, and page-2 generation invokes
        # `_enable_next_link_on_previous_page(output_dir, 1, …)`.
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        def _entry(session_id: str, uuid: str, timestamp: str, text: str) -> dict:
            return {
                "parentUuid": None,
                "isSidechain": False,
                "userType": "external",
                "cwd": "/tmp",
                "sessionId": session_id,
                "version": "2.1.0",
                "type": "user",
                "uuid": uuid,
                "timestamp": timestamp,
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": text}],
                },
            }

        # `_assign_sessions_to_pages` closes a page only when the running
        # message count *strictly exceeds* `page_size`, so session-A needs
        # ≥2 messages to push the count past the threshold and force
        # session-B onto a fresh page (1+1 with page_size=1 still fits in
        # a single page).
        session_a_id = "11111111-1111-1111-1111-111111111111"
        session_b_id = "22222222-2222-2222-2222-222222222222"
        (project_dir / "session-a.jsonl").write_text(
            "\n".join(
                json.dumps(e)
                for e in [
                    _entry(
                        session_a_id,
                        "33333333-3333-3333-3333-333333333333",
                        "2026-05-07T10:00:00.000Z",
                        f"page-1 first msg with {LONE_SURROGATE} (issue #139)",
                    ),
                    _entry(
                        session_a_id,
                        "33333333-3333-3333-3333-333333333334",
                        "2026-05-07T10:00:00.500Z",
                        "page-1 second msg",
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (project_dir / "session-b.jsonl").write_text(
            json.dumps(
                _entry(
                    session_b_id,
                    "44444444-4444-4444-4444-444444444444",
                    "2026-05-07T10:00:01.000Z",
                    "page-2 plain content",
                )
            )
            + "\n",
            encoding="utf-8",
        )

        # Directory mode + page_size=1 forces ≥2 pages; page-2 generation
        # invokes `_enable_next_link_on_previous_page(output_dir, 1, …)`.
        output = convert_jsonl_to_html(project_dir, page_size=1, silent=True)
        assert output.exists()

        # Confirm pagination actually fired (page 2 was created — otherwise
        # the read-rewrite seam wouldn't have been exercised).
        page_1 = project_dir / "combined_transcripts.html"
        page_2 = project_dir / "combined_transcripts_2.html"
        assert page_1.exists() and page_2.exists(), (
            "expected two paginated HTML files; only one was generated"
        )

        # Both pages must be strict-UTF-8 decodable (would raise pre-fix
        # if the surrogate had been written through `write_text` strictly,
        # and the page-1 read in `_enable_next_link_on_previous_page`
        # would also have crashed pre-fix on the same payload).
        page_1_html = page_1.read_bytes().decode("utf-8")
        page_2_html = page_2.read_bytes().decode("utf-8")
        assert LONE_SURROGATE not in page_1_html
        assert LONE_SURROGATE not in page_2_html


@pytest.mark.parametrize(
    "surrogate",
    [
        # Low (surrogateescape) range — what raw-byte decoding produces.
        "\udc80",
        "\udcb2",
        "\udcff",
        # High range — these don't come from surrogateescape decoding but
        # CAN appear from explicit \uD800–\uDBFF JSON escapes upstream.
        # They require a separate scrub mechanism (re.sub) because
        # surrogateescape's encode step doesn't back-map them.
        "\ud800",
        "\udbff",
    ],
)
def test_various_lone_surrogates_handled(tmp_path: Path, surrogate: str):
    """Spot-check across the full lone-surrogate range (U+D800 … U+DFFF)
    that every codepoint encodes safely. Both the surrogateescape-mapped
    low range AND the high range that needs explicit pre-substitution
    are covered."""
    jsonl_path = tmp_path / "session.jsonl"
    user_entry = {
        "parentUuid": None,
        "isSidechain": False,
        "userType": "external",
        "cwd": "/tmp",
        "sessionId": "11111111-1111-1111-1111-111111111111",
        "version": "2.1.0",
        "type": "user",
        "uuid": "22222222-2222-2222-2222-222222222222",
        "timestamp": "2026-05-07T10:00:00.000Z",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": f"x{surrogate}y"}],
        },
    }
    jsonl_path.write_text(json.dumps(user_entry) + "\n", encoding="utf-8")
    output = convert_jsonl_to_html(jsonl_path, silent=True)
    html = output.read_bytes().decode("utf-8")
    assert surrogate not in html
