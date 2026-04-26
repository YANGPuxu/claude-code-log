-- Teammates feature: per-session team name
-- Migration: 005
-- Description: Add a `team_name` column to `sessions` so the project
-- index can surface a "Team: <name>" annotation per project card.
-- Sourced from the first non-None `teamName` of any entry in the
-- session (Claude Code stamps every entry with the same teamName for
-- the duration of a team's activity, so first-sighting-wins is exact).
--
-- Backward-compatible: existing rows get NULL via SQLite's column-add
-- default (and `SessionCacheData.team_name: Optional[str] = None`).
-- Old caches will simply not have team info populated until the next
-- cache rewrite for the affected project.

ALTER TABLE sessions ADD COLUMN team_name TEXT;
