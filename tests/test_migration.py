"""Schema migration: existing rows must survive, because transcripts may not (see store/db.py)."""

from __future__ import annotations

import sqlite3

import pytest

from context_render.errors import PreconditionError
from context_render.store import Store
from context_render.store import db as dbmod
from context_render.store.db import sql_migration


def _db_with_session(tmp_path, sid="s1"):
    """A db at the real current schema, holding one session whose transcript is already gone."""
    db = tmp_path / "db.sqlite"
    store = Store(db)
    store.conn.execute(
        "INSERT INTO sessions(id, project, path, parse_status) VALUES(?, 'p', '/gone.jsonl', 'ok')",
        (sid,),
    )
    store.conn.execute(
        "INSERT INTO usages(session_id, component_id, state, count, confidence, evidence)"
        " VALUES(?, 'skill:x', 'invoked', 3, 'exact', '[]')",
        (sid,),
    )
    store.conn.commit()
    store.close()
    return db


def _v1_db_with_session(tmp_path, sid="s1"):
    """A db at the real historical v1 schema (pre-facts), one session, transcript gone."""
    db = tmp_path / "db.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE sessions (
          id TEXT PRIMARY KEY, project TEXT NOT NULL, path TEXT NOT NULL,
          started_at TEXT, ended_at TEXT, turns INTEGER, cost_usd REAL,
          prompt_digest TEXT, cc_version TEXT,
          parse_status TEXT CHECK(parse_status IN ('ok','degraded')),
          file_mtime TEXT, file_size INTEGER, parsed_at TEXT
        );
        CREATE TABLE usages (
          session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
          component_id TEXT NOT NULL,
          state TEXT NOT NULL CHECK(state IN ('registered','loaded','invoked')),
          count INTEGER NOT NULL DEFAULT 0,
          confidence TEXT NOT NULL CHECK(confidence IN ('exact','heuristic')),
          evidence TEXT,
          PRIMARY KEY (session_id, component_id, state)
        );
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta(key, value) VALUES('schema_version', '1');
        """
    )
    conn.execute(
        "INSERT INTO sessions(id, project, path, parse_status) VALUES(?, 'p', '/gone.jsonl', 'ok')",
        (sid,),
    )
    conn.execute(
        "INSERT INTO usages(session_id, component_id, state, count, confidence, evidence)"
        " VALUES(?, 'skill:x', 'invoked', 3, 'exact', '[]')",
        (sid,),
    )
    conn.commit()
    conn.close()
    return db


def _v2_db_with_session(tmp_path, sid="s1"):
    """A db at the real historical v2 schema (facts, pre component_digests)."""
    db = _v1_db_with_session(tmp_path, sid)
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE facts (
          session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
          idx INTEGER NOT NULL,
          kind TEXT NOT NULL CHECK(kind IN ('search','mapping','chain_read')),
          key TEXT NOT NULL, raw TEXT NOT NULL, tool TEXT,
          tokens_est INTEGER NOT NULL DEFAULT 0, occupancy_est INTEGER,
          sidechain INTEGER NOT NULL DEFAULT 0,
          confidence TEXT NOT NULL CHECK(confidence IN ('exact','heuristic')),
          PRIMARY KEY (session_id, idx, kind, key)
        );
        UPDATE meta SET value='2' WHERE key='schema_version';
        """
    )
    conn.commit()
    conn.close()
    return db


def _next(v: str, n: int = 1) -> str:
    return str(int(v) + n)


def _stamp(db, version):
    conn = sqlite3.connect(db)
    conn.execute("UPDATE meta SET value=? WHERE key='schema_version'", (version,))
    conn.commit()
    conn.close()


def _read(db, query):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(query).fetchall()
    finally:
        conn.close()


def test_real_v1_to_v2_migration_creates_facts_and_keeps_rows(tmp_path, capsys):
    """The shipped v1→v2 step: facts table appears, nothing else is touched."""
    db = _v1_db_with_session(tmp_path)

    store = Store(db)
    try:
        assert store.conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()["value"] == dbmod.SCHEMA_VERSION
        assert store.conn.execute("SELECT id FROM sessions").fetchone()["id"] == "s1"
        assert store.conn.execute("SELECT count FROM usages").fetchone()["count"] == 3
        assert store.conn.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"] == 0
        # a pre-facts session reads as stale so sync backfills it while the transcript lasts
        row = store.conn.execute("SELECT file_mtime, file_size FROM sessions").fetchone()
        from context_render.attributor.facts import FACTS_EXTRACTOR_VERSION
        assert store.needs_update(
            "s1", 0.0, row["file_size"] or 0, FACTS_EXTRACTOR_VERSION) is True
    finally:
        store.close()
    assert len(list(tmp_path.glob("db.sqlite.bak-v1-*"))) == 1
    assert "migrated db.sqlite schema v1" in capsys.readouterr().err


def test_real_v2_to_v3_migration_creates_component_digests_and_keeps_rows(tmp_path, capsys):
    """The shipped v2→v3 step: component_digests appears, nothing else is touched."""
    db = _v2_db_with_session(tmp_path)

    store = Store(db)
    try:
        assert store.conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()["value"] == dbmod.SCHEMA_VERSION
        assert store.conn.execute("SELECT id FROM sessions").fetchone()["id"] == "s1"
        assert store.conn.execute("SELECT count FROM usages").fetchone()["count"] == 3
        assert store.conn.execute(
            "SELECT COUNT(*) c FROM component_digests").fetchone()["c"] == 0
    finally:
        store.close()
    assert len(list(tmp_path.glob("db.sqlite.bak-v2-*"))) == 1
    assert "migrated db.sqlite schema v2" in capsys.readouterr().err


def test_migration_upgrades_in_place_and_keeps_rows(tmp_path, monkeypatch, capsys):
    db = _db_with_session(tmp_path)
    cur = dbmod.SCHEMA_VERSION
    nxt = _next(cur)
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", nxt)
    monkeypatch.setattr(
        dbmod, "MIGRATIONS",
        {cur: (nxt, sql_migration("ALTER TABLE sessions ADD COLUMN model TEXT"))},
    )

    store = Store(db)
    try:
        assert store.conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()["value"] == nxt
        row = store.conn.execute("SELECT id, model FROM sessions").fetchone()
        assert row["id"] == "s1" and row["model"] is None
        assert store.conn.execute("SELECT count FROM usages").fetchone()["count"] == 3
    finally:
        store.close()

    backups = list(tmp_path.glob(f"db.sqlite.bak-v{cur}-*"))
    assert len(backups) == 1, "the pre-migration file must be kept"
    assert _read(backups[0], "SELECT id FROM sessions")[0][0] == "s1"
    assert f"migrated db.sqlite schema v{cur} → v{nxt}" in capsys.readouterr().err


def test_migration_chains_multiple_steps(tmp_path, monkeypatch):
    db = _db_with_session(tmp_path)
    cur = dbmod.SCHEMA_VERSION
    v2, v3 = _next(cur), _next(cur, 2)
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", v3)
    monkeypatch.setattr(
        dbmod, "MIGRATIONS",
        {
            cur: (v2, sql_migration("ALTER TABLE sessions ADD COLUMN model TEXT")),
            v2: (v3, sql_migration("UPDATE sessions SET model='opus'")),
        },
    )

    store = Store(db)
    try:
        assert store.conn.execute("SELECT model FROM sessions").fetchone()["model"] == "opus"
        assert store.conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()["value"] == v3
    finally:
        store.close()


def test_missing_migration_refuses_and_leaves_db_untouched(tmp_path, monkeypatch):
    db = _db_with_session(tmp_path)
    cur = dbmod.SCHEMA_VERSION
    nxt = _next(cur)
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", nxt)
    monkeypatch.setattr(dbmod, "MIGRATIONS", {})  # gap: nothing knows how to reach nxt

    with pytest.raises(PreconditionError, match=f"no migration from schema v{cur}"):
        Store(db)

    assert _read(db, "SELECT value FROM meta WHERE key='schema_version'")[0][0] == cur
    assert _read(db, "SELECT id FROM sessions")[0][0] == "s1"
    assert not list(tmp_path.glob("db.sqlite.bak-*")), "must not touch the file it cannot migrate"


def test_failed_migration_rolls_back_and_keeps_backup(tmp_path, monkeypatch):
    db = _db_with_session(tmp_path)
    cur = dbmod.SCHEMA_VERSION
    nxt = _next(cur)
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", nxt)
    monkeypatch.setattr(
        dbmod, "MIGRATIONS",
        {cur: (nxt, sql_migration(
            "ALTER TABLE sessions ADD COLUMN model TEXT",
            "UPDATE sessions SET nonexistent_column = 1",  # blows up mid-step
        ))},
    )

    with pytest.raises(PreconditionError, match="rolled back and is unchanged"):
        Store(db)

    assert _read(db, "SELECT value FROM meta WHERE key='schema_version'")[0][0] == cur
    assert _read(db, "SELECT id FROM sessions")[0][0] == "s1"
    assert [c[1] for c in _read(db, "PRAGMA table_info(sessions)")].count("model") == 0
    assert len(list(tmp_path.glob(f"db.sqlite.bak-v{cur}-*"))) == 1


def test_db_from_newer_build_is_not_downgraded(tmp_path):
    db = _db_with_session(tmp_path)
    _stamp(db, "99")

    with pytest.raises(PreconditionError, match="Upgrade context-render"):
        Store(db)

    assert _read(db, "SELECT id FROM sessions")[0][0] == "s1"
    assert not list(tmp_path.glob("db.sqlite.bak-*"))


def _v3_db_with_session(tmp_path, sid="s1"):
    """A db at the real historical v3 schema (component_digests, pre stale_windows)."""
    db = _v2_db_with_session(tmp_path, sid)
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE component_digests (
          component_id TEXT NOT NULL, digest TEXT NOT NULL,
          file_mtime TEXT, first_seen TEXT NOT NULL,
          PRIMARY KEY (component_id, first_seen)
        );
        UPDATE meta SET value='3' WHERE key='schema_version';
        """
    )
    conn.commit()
    conn.close()
    return db


def test_real_v3_to_v4_migration_creates_stale_windows_and_keeps_rows(tmp_path, capsys):
    """The shipped v3→v4 step: stale_windows appears, nothing else is touched."""
    db = _v3_db_with_session(tmp_path)

    store = Store(db)
    try:
        assert store.conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()["value"] == dbmod.SCHEMA_VERSION
        assert store.conn.execute("SELECT id FROM sessions").fetchone()["id"] == "s1"
        assert store.conn.execute("SELECT count FROM usages").fetchone()["count"] == 3
        assert store.conn.execute(
            "SELECT COUNT(*) c FROM stale_windows").fetchone()["c"] == 0
        # a pre-stale session reads as stale so sync backfills it while the transcript lasts
        from context_render.attributor.facts import FACTS_EXTRACTOR_VERSION
        from context_render.attributor.stale import STALE_EXTRACTOR_VERSION
        row = store.conn.execute("SELECT file_mtime, file_size FROM sessions").fetchone()
        assert store.needs_update(
            "s1", 0.0, row["file_size"] or 0,
            FACTS_EXTRACTOR_VERSION, STALE_EXTRACTOR_VERSION) is True
    finally:
        store.close()
    assert len(list(tmp_path.glob("db.sqlite.bak-v3-*"))) == 1
    assert "migrated db.sqlite schema v3" in capsys.readouterr().err


def test_shipped_migrations_form_a_complete_chain():
    """Every registered step must actually reach SCHEMA_VERSION — no dead ends, no cycles."""
    for start in dbmod.MIGRATIONS:
        seen, v = set(), start
        while v != dbmod.SCHEMA_VERSION:
            assert v not in seen, f"cycle in MIGRATIONS at v{v}"
            seen.add(v)
            assert v in dbmod.MIGRATIONS, f"MIGRATIONS dead-ends at v{v}"
            v = dbmod.MIGRATIONS[v][0]
