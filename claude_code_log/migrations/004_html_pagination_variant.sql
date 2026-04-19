-- Variant-aware HTML pagination
-- Migration: 004
-- Description: Add variant_suffix column so different output variants
-- (--detail full/high/low/minimal, optionally combined with --compact)
-- get independent cache entries. Previously UNIQUE(project_id, page_number)
-- allowed a second variant's render to clobber the first's cache rows;
-- per-filename staleness lookups then crossed variants indefinitely.
--
-- SQLite cannot drop a UNIQUE constraint in place, so the whole table is
-- recreated. Existing rows get variant_suffix = '' (the full/default
-- variant), which matches their current html_path semantics. The `id`
-- column is preserved so the page_sessions foreign key stays intact.
--
-- `page_sessions.page_id` has `ON DELETE CASCADE` referencing
-- `html_pages.id`. With `foreign_keys = ON` (set globally by runner.py),
-- the `DROP TABLE html_pages` step below would cascade and wipe every
-- page_sessions row BEFORE the RENAME restores the target. Follow the
-- official SQLite 12-step ALTER TABLE recipe: disable FK enforcement
-- around the recreate and re-enable + audit afterwards.

PRAGMA foreign_keys = OFF;

CREATE TABLE html_pages_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    variant_suffix TEXT NOT NULL DEFAULT '',  -- e.g. "", ".low", ".low.compact"
    page_number INTEGER NOT NULL,
    html_path TEXT NOT NULL,
    page_size_config INTEGER NOT NULL,
    message_count INTEGER NOT NULL,
    first_session_id TEXT NOT NULL,
    last_session_id TEXT NOT NULL,
    first_timestamp TEXT,
    last_timestamp TEXT,
    total_input_tokens INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    total_cache_creation_tokens INTEGER DEFAULT 0,
    total_cache_read_tokens INTEGER DEFAULT 0,
    generated_at TEXT NOT NULL,
    library_version TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(project_id, variant_suffix, page_number)
);

INSERT INTO html_pages_new (
    id, project_id, variant_suffix, page_number, html_path,
    page_size_config, message_count, first_session_id, last_session_id,
    first_timestamp, last_timestamp,
    total_input_tokens, total_output_tokens,
    total_cache_creation_tokens, total_cache_read_tokens,
    generated_at, library_version
)
SELECT
    id, project_id, '', page_number, html_path,
    page_size_config, message_count, first_session_id, last_session_id,
    first_timestamp, last_timestamp,
    total_input_tokens, total_output_tokens,
    total_cache_creation_tokens, total_cache_read_tokens,
    generated_at, library_version
FROM html_pages;

DROP TABLE html_pages;
ALTER TABLE html_pages_new RENAME TO html_pages;

CREATE INDEX IF NOT EXISTS idx_html_pages_project ON html_pages(project_id);

-- Verify no FK violations were introduced, then restore enforcement.
PRAGMA foreign_key_check;
PRAGMA foreign_keys = ON;
