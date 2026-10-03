"""SQLite access and idempotent writes.

Idempotent: session id is the primary key; BEGIN → DELETE (cascade clears usages) → re-insert → COMMIT.
Aggregation always recomputes from the DB; no aggregate snapshot is stored.

This DB is an archive, not a cache. Claude Code expires transcripts on a rolling window
(`cleanupPeriodDays`, default 30 days), so `~/.claude/projects/**/*.jsonl` is a buffer, not a
source of truth: once a session's transcript is expired, the rows here are the only surviving
record of it and no rescan can bring it back. A full re-parse is cheap (well under a second) but
it can only ever recover sessions whose transcripts still exist — which is exactly why "delete
the DB and rebuild" is never a valid repair. Migrate in place instead; treat every existing row
as unreproducible.

Internal reserved component ids (underscore-prefixed, excluded from the component list during aggregation):
  _event:git_commit  — number of git commit events in the session (for the hook MISS verdict)
  _cost:static       — session-level cost detail (evidence JSON: output of the cost engine)
  _facts:extract     — facts-extraction marker: written for every session ingested by a
                       facts-aware build (evidence JSON: {"facts": n, "tool_output_tokens_est": m}).
                       A session without it was ingested before the facts feature — sync backfills
                       it while the transcript still exists; sessions whose transcripts expired
                       first stay marker-less forever (report counts them outside facts coverage).
  _stale:extract     — stale-gauge extraction marker (evidence JSON: {"stale_windows": n, "extractor": v}); same backfill semantics as _facts:extract.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from ..errors import PreconditionError

SCHEMA_VERSION = "4"

FACTS_CID = "_facts:extract"
STALE_CID = "_stale:extract"

Migration = Callable[[sqlite3.Connection], None]


def sql_migration(*statements: str) -> Migration:
    def run(conn: sqlite3.Connection) -> None:
        for stmt in statements:
            conn.execute(stmt)

    return run


# Upgrade path, keyed by the version found in the file: from_version -> (to_version, step).
# Store._migrate chains these until it reaches SCHEMA_VERSION, after backing the file up.
#
# When you change schema.sql, add the matching step here in the same commit — an unmigratable
# DB is a data-loss event, not an inconvenience: sessions whose transcripts Claude Code has
# already expired exist nowhere else (see the module docstring), so "rebuild it" cannot get
# them back. Steps are append-only: never rewrite one that has shipped, and never drop rows.
# Re-derive new columns from the rows already present (evidence is stored as JSON for exactly
# this reason) rather than from transcripts, which may be gone.
#
#   MIGRATIONS = {"1": ("2", sql_migration("ALTER TABLE sessions ADD COLUMN model TEXT"))}
MIGRATIONS: dict[str, tuple[str, Migration]] = {
    # v2: facts table (self-derivation extraction).
    # Existing rows untouched; old sessions get facts backfilled by sync while their
    # transcripts still exist (needs_update treats a missing _facts:extract marker as stale).
    "1": (
        "2",
        sql_migration(
            """CREATE TABLE IF NOT EXISTS facts (
                 session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                 idx           INTEGER NOT NULL,
                 kind          TEXT NOT NULL CHECK(kind IN ('search','mapping','chain_read')),
                 key           TEXT NOT NULL,
                 raw           TEXT NOT NULL,
                 tool          TEXT,
                 tokens_est    INTEGER NOT NULL DEFAULT 0,
                 occupancy_est INTEGER,
                 sidechain     INTEGER NOT NULL DEFAULT 0,
                 confidence    TEXT NOT NULL CHECK(confidence IN ('exact','heuristic')),
                 PRIMARY KEY (session_id, idx, kind, key)
               )"""
        ),
    ),
    # v3: component_digests table (edit-epoch tracking for the since-removed `component`
    # view). The step is kept verbatim — shipped steps are append-only — and the table
    # stays in schema.sql so fresh and migrated DBs match; nothing writes it anymore.
    "2": (
        "3",
        sql_migration(
            """CREATE TABLE IF NOT EXISTS component_digests (
                 component_id TEXT NOT NULL,
                 digest       TEXT NOT NULL,
                 file_mtime   TEXT,
                 first_seen   TEXT NOT NULL,
                 PRIMARY KEY (component_id, first_seen)
               )"""
        ),
    ),
    # v4: stale_windows table (stale gauge).
    # Existing rows untouched; old sessions get stale windows backfilled by sync while
    # their transcripts still exist (needs_update treats a missing _stale:extract marker
    # as stale).
    "3": (
        "4",
        sql_migration(
            """CREATE TABLE IF NOT EXISTS stale_windows (
                 session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                 window_side  INTEGER NOT NULL DEFAULT 0,
                 window_agent TEXT NOT NULL DEFAULT '',
                 path         TEXT NOT NULL,
                 read_idx     INTEGER NOT NULL,
                 mutate_idx   INTEGER NOT NULL,
                 mutate_tool  TEXT NOT NULL,
                 close_idx    INTEGER,
                 outcome      TEXT NOT NULL CHECK(outcome IN ('re-read','compacted','never-re-read')),
                 read_tokens_est INTEGER NOT NULL DEFAULT 0,
                 read_partial INTEGER NOT NULL DEFAULT 0,
                 confidence   TEXT NOT NULL CHECK(confidence IN ('exact','heuristic')),
                 PRIMARY KEY (session_id, window_side, window_agent, path, read_idx, mutate_idx)
               )"""
        ),
    ),
}


def _placeholders(n: int) -> str:
    """`?,?,…` for an `IN (…)` list of n bound parameters."""
    return ",".join("?" * n)


def _version_num(v: str, where: str) -> int:
    try:
        return int(v)
    except ValueError as e:
        raise PreconditionError(
            f"unreadable schema version {v!r} in {where}; db.sqlite may be damaged") from e


class Store:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self) -> None:
        try:
            fresh = (
                self.conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
                ).fetchone()
                is None
            )
            if fresh:
                # schema.sql describes the current version only; existing files reach it by migration.
                self.conn.executescript((Path(__file__).parent / "schema.sql").read_text(encoding="utf-8"))
                self.conn.execute(
                    "INSERT INTO meta(key, value) VALUES('schema_version', ?)", (SCHEMA_VERSION,)
                )
                self.conn.commit()
                return
            row = self.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        except sqlite3.DatabaseError as e:
            raise PreconditionError(
                f"DB corrupt or could not initialize: {e}\n"
                "Do not delete db.sqlite to 'fix' this: sessions whose transcripts Claude Code has "
                "already expired (cleanupPeriodDays, default 30d) exist nowhere else and a rescan "
                "cannot recover them.\n"
                "Copy db.sqlite aside first, then try to salvage it: "
                "sqlite3 db.sqlite '.recover' | sqlite3 recovered.sqlite"
            ) from e

        if row is None:  # pre-versioning file: adopt it at the current version
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?)", (SCHEMA_VERSION,)
            )
            self.conn.commit()
        elif row["value"] != SCHEMA_VERSION:
            self._migrate(row["value"])

    def _migrate(self, found: str) -> None:
        if _version_num(found, "db.sqlite") > _version_num(SCHEMA_VERSION, "this build"):
            raise PreconditionError(
                f"db.sqlite is schema v{found}, but this context-render only knows v{SCHEMA_VERSION} "
                "— it was written by a newer version.\n"
                "Upgrade context-render. Do not delete the DB to make this go away: it holds sessions "
                "whose transcripts Claude Code has already expired (cleanupPeriodDays, default 30d), "
                "and no rescan can recover those."
            )

        # Plan the whole chain before touching the file, so a gap fails with the DB untouched.
        chain: list[tuple[str, str, Migration]] = []
        v = found
        while v != SCHEMA_VERSION:
            step = MIGRATIONS.get(v)
            if step is None:
                raise PreconditionError(
                    f"no migration from schema v{v} to v{SCHEMA_VERSION}; this build cannot upgrade "
                    f"db.sqlite (found v{found}).\n"
                    "This is a bug in context-render, not something to fix by deleting the DB: it holds "
                    "sessions whose transcripts Claude Code has already expired (cleanupPeriodDays, "
                    "default 30d), and a rescan cannot bring those back. Copy db.sqlite somewhere safe "
                    "and report the version pair above."
                )
            to_v, fn = step
            if any(to_v == seen_from for seen_from, _, _ in chain) or to_v == v:
                raise PreconditionError(f"migration cycle in MIGRATIONS at v{v} (bug)")
            chain.append((v, to_v, fn))
            v = to_v

        backup = self.db_path.parent / (
            f"{self.db_path.name}.bak-v{found}-{datetime.now().astimezone().strftime('%Y%m%d%H%M%S')}"
        )
        self.conn.commit()
        shutil.copy2(self.db_path, backup)

        try:
            self.conn.execute("BEGIN")
            for _from_v, to_v, fn in chain:
                fn(self.conn)
                self.conn.execute("UPDATE meta SET value=? WHERE key='schema_version'", (to_v,))
            self.conn.commit()
        except sqlite3.DatabaseError as e:
            self.conn.rollback()
            raise PreconditionError(
                f"migration v{found} → v{SCHEMA_VERSION} failed: {e}\n"
                f"db.sqlite was rolled back and is unchanged; a pre-migration copy is at {backup}."
            ) from e
        print(
            f"migrated db.sqlite schema v{found} → v{SCHEMA_VERSION} (backup: {backup.name})",
            file=sys.stderr,
        )

    def close(self) -> None:
        self.conn.close()

    # ---- idempotent writes ----

    def needs_update(self, session_id: str, mtime: float, size: int,
                     extractor_version: int, stale_extractor_version: int = 0) -> bool:
        row = self.conn.execute(
            "SELECT file_mtime, file_size FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        if row is None:
            return True
        if row["file_mtime"] != repr(mtime) or row["file_size"] != size:
            return True
        # ingested by an older extractor → stale while the transcript still exists;
        # missing marker = pre-facts build, missing field = version 1 build
        marker = self.conn.execute(
            "SELECT evidence FROM usages WHERE session_id=? AND component_id=?",
            (session_id, FACTS_CID),
        ).fetchone()
        if marker is None:
            return True
        try:
            ev = json.loads(marker["evidence"] or "{}")
        except json.JSONDecodeError:
            return True
        if int(ev.get("extractor") or 1) < extractor_version:
            return True
        if stale_extractor_version <= 0:
            return False  # caller opted out of the stale gate
        smarker = self.conn.execute(
            "SELECT evidence FROM usages WHERE session_id=? AND component_id=?",
            (session_id, STALE_CID),
        ).fetchone()
        if smarker is None:
            return True  # pre-stale build → backfill while the transcript exists
        try:
            sev = json.loads(smarker["evidence"] or "{}")
        except json.JSONDecodeError:
            return True
        return int(sev.get("extractor") or 0) < stale_extractor_version

    def has_session(self, session_id: str) -> bool:
        return (
            self.conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone()
            is not None
        )

    def replace_session(self, session_row: dict, usage_rows: list[dict],
                        fact_rows: list[dict] | None = None,
                        stale_rows: list[dict] | None = None) -> None:
        """Transactional whole-session replacement (re-runs don't double-count);
        the DELETE cascades over usages, facts and stale_windows alike."""
        cur = self.conn.cursor()
        try:
            cur.execute("BEGIN")
            cur.execute("DELETE FROM sessions WHERE id=?", (session_row["id"],))
            cur.execute(
                """INSERT INTO sessions(id, project, path, started_at, ended_at, turns,
                       cost_usd, prompt_digest, cc_version, parse_status,
                       file_mtime, file_size, parsed_at)
                   VALUES(:id,:project,:path,:started_at,:ended_at,:turns,
                          :cost_usd,:prompt_digest,:cc_version,:parse_status,
                          :file_mtime,:file_size,:parsed_at)""",
                session_row,
            )
            # executemany: same rows, same order (rowids), same exception on a bad row —
            # one prepared statement per table instead of one per row
            cur.executemany(
                """INSERT INTO usages(session_id, component_id, state, count,
                       confidence, evidence)
                   VALUES(:session_id,:component_id,:state,:count,:confidence,:evidence)""",
                usage_rows,
            )
            cur.executemany(
                """INSERT INTO facts(session_id, idx, kind, key, raw, tool,
                       tokens_est, occupancy_est, sidechain, confidence)
                   VALUES(:session_id,:idx,:kind,:key,:raw,:tool,
                          :tokens_est,:occupancy_est,:sidechain,:confidence)""",
                fact_rows or [],
            )
            cur.executemany(
                """INSERT INTO stale_windows(session_id, window_side, window_agent,
                       path, read_idx, mutate_idx, mutate_tool, close_idx, outcome,
                       read_tokens_est, read_partial, confidence)
                   VALUES(:session_id,:window_side,:window_agent,:path,:read_idx,
                          :mutate_idx,:mutate_tool,:close_idx,:outcome,
                          :read_tokens_est,:read_partial,:confidence)""",
                stale_rows or [],
            )
            self.conn.commit()
        except sqlite3.DatabaseError:
            self.conn.rollback()
            raise

    # ---- queries ----

    def unreproducible_sessions(self) -> list[sqlite3.Row]:
        """Sessions whose transcript is no longer on disk — sync can never recreate these rows.

        Claude Code expires transcripts on a rolling window (cleanupPeriodDays, default 30d).
        For these sessions the DB is the last surviving copy; callers about to destroy rows
        (clear, a --force rebuild, a schema migration) must say so before they do it.
        """
        return [
            r
            for r in self.conn.execute(
                "SELECT id, started_at, path FROM sessions ORDER BY started_at"
            )
            if not Path(r["path"]).exists()
        ]

    def sessions_since(self, since_iso: str | None) -> list[sqlite3.Row]:
        if since_iso:
            return self.conn.execute(
                "SELECT * FROM sessions WHERE started_at >= ? ORDER BY started_at",
                (since_iso,),
            ).fetchall()
        return self.conn.execute("SELECT * FROM sessions ORDER BY started_at").fetchall()

    def usages_for_sessions(self, session_ids: list[str]) -> list[sqlite3.Row]:
        if not session_ids:
            return []
        qs = _placeholders(len(session_ids))
        return self.conn.execute(
            f"SELECT * FROM usages WHERE session_id IN ({qs})", session_ids
        ).fetchall()

    def facts_for_sessions(self, session_ids: list[str]) -> list[sqlite3.Row]:
        if not session_ids:
            return []
        qs = _placeholders(len(session_ids))
        return self.conn.execute(
            f"SELECT * FROM facts WHERE session_id IN ({qs}) ORDER BY session_id, idx",
            session_ids,
        ).fetchall()

    def stale_for_sessions(self, session_ids: list[str]) -> list[sqlite3.Row]:
        if not session_ids:
            return []
        qs = _placeholders(len(session_ids))
        return self.conn.execute(
            f"SELECT * FROM stale_windows WHERE session_id IN ({qs})"
            f" ORDER BY session_id, mutate_idx, read_idx, path",
            session_ids,
        ).fetchall()

    def usages_for_component(self, component_id: str,
                            session_ids: list[str]) -> list[sqlite3.Row]:
        if not session_ids:
            return []
        qs = _placeholders(len(session_ids))
        return self.conn.execute(
            f"SELECT * FROM usages WHERE component_id=? AND session_id IN ({qs})",
            [component_id, *session_ids],
        ).fetchall()

    def facts_coverage(self, session_ids: list[str]) -> tuple[set[str], int]:
        """(sessions whose facts were extracted, Σ tool-output token estimate over them).

        Extraction is marked by the _facts:extract usage row; sessions ingested before
        the facts feature whose transcripts already expired can never be backfilled —
        they count in the window but not in the facts coverage."""
        if not session_ids:
            return set(), 0
        qs = _placeholders(len(session_ids))
        covered: set[str] = set()
        tool_output = 0
        for r in self.conn.execute(
            f"SELECT session_id, evidence FROM usages"
            f" WHERE component_id=? AND session_id IN ({qs})",
            [FACTS_CID, *session_ids],
        ):
            covered.add(r["session_id"])
            try:
                d = json.loads(r["evidence"] or "{}")
            except json.JSONDecodeError:
                d = {}
            tool_output += int(d.get("tool_output_tokens_est") or 0)
        return covered, tool_output

    def snapshot(self) -> list[tuple]:
        """For testing: snapshot of all DB content (excluding timestamps like parsed_at)."""
        rows: list[tuple] = []
        for query in (
            "SELECT id, project, started_at, ended_at, turns, cost_usd, prompt_digest,"
            " cc_version, parse_status, file_size FROM sessions ORDER BY id",
            "SELECT session_id, component_id, state, count, confidence, evidence"
            " FROM usages ORDER BY session_id, component_id, state",
            "SELECT session_id, idx, kind, key, raw, tool, tokens_est, occupancy_est,"
            " sidechain, confidence FROM facts ORDER BY session_id, idx, kind, key",
            "SELECT session_id, window_side, window_agent, path, read_idx, mutate_idx,"
            " mutate_tool, close_idx, outcome, read_tokens_est, read_partial, confidence"
            " FROM stale_windows ORDER BY session_id, mutate_idx, read_idx, path",
        ):
            rows.extend(tuple(r) for r in self.conn.execute(query))
        return rows


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def dumps_evidence(evidence: list[dict]) -> str:
    return json.dumps(evidence, ensure_ascii=False, sort_keys=True)
