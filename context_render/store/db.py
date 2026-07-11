"""SQLite access and idempotent writes (A8).

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
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from ..errors import PreconditionError

SCHEMA_VERSION = "1"

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
MIGRATIONS: dict[str, tuple[str, Migration]] = {}


def _version_num(v: str, where: str) -> int:
    try:
        return int(v)
    except ValueError:
        raise PreconditionError(f"unreadable schema version {v!r} in {where}; db.sqlite may be damaged")


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
            )

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
            f"{self.db_path.name}.bak-v{found}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
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
            )
        print(
            f"migrated db.sqlite schema v{found} → v{SCHEMA_VERSION} (backup: {backup.name})",
            file=sys.stderr,
        )

    def close(self) -> None:
        self.conn.close()

    # ---- idempotent writes ----

    def needs_update(self, session_id: str, mtime: float, size: int) -> bool:
        row = self.conn.execute(
            "SELECT file_mtime, file_size FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        if row is None:
            return True
        return row["file_mtime"] != repr(mtime) or row["file_size"] != size

    def has_session(self, session_id: str) -> bool:
        return (
            self.conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone()
            is not None
        )

    def replace_session(self, session_row: dict, usage_rows: list[dict]) -> None:
        """Transactional whole-session replacement (re-runs don't double-count, AC3)."""
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
            for u in usage_rows:
                cur.execute(
                    """INSERT INTO usages(session_id, component_id, state, count,
                           confidence, evidence)
                       VALUES(:session_id,:component_id,:state,:count,:confidence,:evidence)""",
                    u,
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
        qs = ",".join("?" * len(session_ids))
        return self.conn.execute(
            f"SELECT * FROM usages WHERE session_id IN ({qs})", session_ids
        ).fetchall()

    def snapshot(self) -> list[tuple]:
        """For testing: snapshot of all DB content (excluding timestamps like parsed_at)."""
        rows = []
        for r in self.conn.execute(
            "SELECT id, project, started_at, ended_at, turns, cost_usd, prompt_digest,"
            " cc_version, parse_status, file_size FROM sessions ORDER BY id"
        ):
            rows.append(tuple(r))
        for r in self.conn.execute(
            "SELECT session_id, component_id, state, count, confidence, evidence"
            " FROM usages ORDER BY session_id, component_id, state"
        ):
            rows.append(tuple(r))
        return rows


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps_evidence(evidence: list[dict]) -> str:
    return json.dumps(evidence, ensure_ascii=False, sort_keys=True)
