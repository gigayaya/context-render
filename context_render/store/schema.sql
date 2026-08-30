-- context-render DDL (appendix A8.1 final; v4: stale_windows)
CREATE TABLE IF NOT EXISTS sessions (
  id            TEXT PRIMARY KEY,          -- transcript filename UUID
  project       TEXT NOT NULL,             -- project name inferred from cwd (reserved for M3 cross-project aggregation)
  path          TEXT NOT NULL,             -- transcript absolute path
  started_at    TEXT, ended_at TEXT,
  turns         INTEGER, cost_usd REAL,    -- cost_usd may be NULL (subscription / no usage)
  prompt_digest TEXT, cc_version TEXT,
  parse_status  TEXT CHECK(parse_status IN ('ok','degraded')),
  file_mtime    TEXT, file_size INTEGER, parsed_at TEXT
);
CREATE TABLE IF NOT EXISTS usages (
  session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  component_id TEXT NOT NULL,
  state        TEXT NOT NULL CHECK(state IN ('registered','loaded','invoked')),
  count        INTEGER NOT NULL DEFAULT 0,
  confidence   TEXT NOT NULL CHECK(confidence IN ('exact','heuristic')),
  evidence     TEXT,                        -- JSON array
  PRIMARY KEY (session_id, component_id, state)
);
CREATE TABLE IF NOT EXISTS facts (
  session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  idx           INTEGER NOT NULL,   -- merged-stream event number (evidence ref, W2 rule)
  kind          TEXT NOT NULL CHECK(kind IN ('search','mapping','chain_read')),
  key           TEXT NOT NULL,      -- canonical key; mapping is always 'repo layout'
  raw           TEXT NOT NULL,      -- original pattern / command segment (never user prompt text);
                                    -- chain_read: read path, repo-relative since extractor v3
  tool          TEXT,               -- Grep | Glob | Bash | Read | bash search head (grep, rg, …)
  tokens_est    INTEGER NOT NULL DEFAULT 0,
  occupancy_est INTEGER,            -- NULL = not computable
  sidechain     INTEGER NOT NULL DEFAULT 0,
  confidence    TEXT NOT NULL CHECK(confidence IN ('exact','heuristic')),
  PRIMARY KEY (session_id, idx, kind, key)
);
-- Retained for schema-v3 compatibility only (fresh and migrated DBs must match):
-- the `component` view that wrote edit-epoch rows here was removed in v0.5.0.
CREATE TABLE IF NOT EXISTS component_digests (
  component_id TEXT NOT NULL,      -- manifest component id
  digest       TEXT NOT NULL,      -- content sha1[:12] at observation
  file_mtime   TEXT,               -- content file mtime (ISO UTC) when observed; edit-time hint
  first_seen   TEXT NOT NULL,      -- when sync first observed this content version
  PRIMARY KEY (component_id, first_seen)
);
-- stale gauge (design specs/2026-08-01-stale-gauge-design.md §3): one row per
-- "read → mutated → (re-read | compacted | never re-read)" span per context window
CREATE TABLE IF NOT EXISTS stale_windows (
  session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  window_side  INTEGER NOT NULL DEFAULT 0,   -- sidechain flag
  window_agent TEXT NOT NULL DEFAULT '',      -- sidechain identity; '' = main chain
                                              -- (NOT NULL: NULL in a non-rowid PK breaks uniqueness)
  path         TEXT NOT NULL,                 -- repo-relative
  read_idx     INTEGER NOT NULL,
  mutate_idx   INTEGER NOT NULL,
  mutate_tool  TEXT NOT NULL,                 -- Edit | Write | NotebookEdit | bash command head
  close_idx    INTEGER,                       -- NULL = never-re-read (closed by session end)
  outcome      TEXT NOT NULL CHECK(outcome IN ('re-read','compacted','never-re-read')),
  read_tokens_est INTEGER NOT NULL DEFAULT 0, -- size of the overturned copy (read result tokens)
  read_partial INTEGER NOT NULL DEFAULT 0,    -- opening read carried offset/limit (archived only)
  confidence   TEXT NOT NULL CHECK(confidence IN ('exact','heuristic')),
  PRIMARY KEY (session_id, window_side, window_agent, path, read_idx, mutate_idx)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
