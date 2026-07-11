"""Schema migration: existing rows must survive, because transcripts may not (see store/db.py)."""

from __future__ import annotations

import sqlite3

import pytest

from context_render.errors import PreconditionError
from context_render.store import Store
from context_render.store import db as dbmod
from context_render.store.db import sql_migration


def _v1_db_with_session(tmp_path, sid="s1"):
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


def test_migration_upgrades_in_place_and_keeps_rows(tmp_path, monkeypatch, capsys):
    db = _v1_db_with_session(tmp_path)
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", "2")
    monkeypatch.setattr(
        dbmod, "MIGRATIONS",
        {"1": ("2", sql_migration("ALTER TABLE sessions ADD COLUMN model TEXT"))},
    )

    store = Store(db)
    try:
        assert store.conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()["value"] == "2"
        row = store.conn.execute("SELECT id, model FROM sessions").fetchone()
        assert row["id"] == "s1" and row["model"] is None
        assert store.conn.execute("SELECT count FROM usages").fetchone()["count"] == 3
    finally:
        store.close()

    backups = list(tmp_path.glob("db.sqlite.bak-v1-*"))
    assert len(backups) == 1, "the pre-migration file must be kept"
    assert _read(backups[0], "SELECT id FROM sessions")[0][0] == "s1"
    assert "migrated db.sqlite schema v1 → v2" in capsys.readouterr().err


def test_migration_chains_multiple_steps(tmp_path, monkeypatch):
    db = _v1_db_with_session(tmp_path)
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", "3")
    monkeypatch.setattr(
        dbmod, "MIGRATIONS",
        {
            "1": ("2", sql_migration("ALTER TABLE sessions ADD COLUMN model TEXT")),
            "2": ("3", sql_migration("UPDATE sessions SET model='opus'")),
        },
    )

    store = Store(db)
    try:
        assert store.conn.execute("SELECT model FROM sessions").fetchone()["model"] == "opus"
        assert store.conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()["value"] == "3"
    finally:
        store.close()


def test_missing_migration_refuses_and_leaves_db_untouched(tmp_path, monkeypatch):
    db = _v1_db_with_session(tmp_path)
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", "2")
    monkeypatch.setattr(dbmod, "MIGRATIONS", {})  # gap: nothing knows how to reach v2

    with pytest.raises(PreconditionError, match="no migration from schema v1"):
        Store(db)

    assert _read(db, "SELECT value FROM meta WHERE key='schema_version'")[0][0] == "1"
    assert _read(db, "SELECT id FROM sessions")[0][0] == "s1"
    assert not list(tmp_path.glob("db.sqlite.bak-*")), "must not touch the file it cannot migrate"


def test_failed_migration_rolls_back_and_keeps_backup(tmp_path, monkeypatch):
    db = _v1_db_with_session(tmp_path)
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", "2")
    monkeypatch.setattr(
        dbmod, "MIGRATIONS",
        {"1": ("2", sql_migration(
            "ALTER TABLE sessions ADD COLUMN model TEXT",
            "UPDATE sessions SET nonexistent_column = 1",  # blows up mid-step
        ))},
    )

    with pytest.raises(PreconditionError, match="rolled back and is unchanged"):
        Store(db)

    assert _read(db, "SELECT value FROM meta WHERE key='schema_version'")[0][0] == "1"
    assert _read(db, "SELECT id FROM sessions")[0][0] == "s1"
    assert [c[1] for c in _read(db, "PRAGMA table_info(sessions)")].count("model") == 0
    assert len(list(tmp_path.glob("db.sqlite.bak-v1-*"))) == 1


def test_db_from_newer_build_is_not_downgraded(tmp_path):
    db = _v1_db_with_session(tmp_path)
    _stamp(db, "99")

    with pytest.raises(PreconditionError, match="Upgrade context-render"):
        Store(db)

    assert _read(db, "SELECT id FROM sessions")[0][0] == "s1"
    assert not list(tmp_path.glob("db.sqlite.bak-*"))


def test_shipped_migrations_form_a_complete_chain():
    """Every registered step must actually reach SCHEMA_VERSION — no dead ends, no cycles."""
    for start in dbmod.MIGRATIONS:
        seen, v = set(), start
        while v != dbmod.SCHEMA_VERSION:
            assert v not in seen, f"cycle in MIGRATIONS at v{v}"
            seen.add(v)
            assert v in dbmod.MIGRATIONS, f"MIGRATIONS dead-ends at v{v}"
            v = dbmod.MIGRATIONS[v][0]
