-- context-render DDL (appendix A8.1 final; v2: no violations table)
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
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
