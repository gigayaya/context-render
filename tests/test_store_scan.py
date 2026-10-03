"""scan idempotency: re-running on same data gives equal DB snapshot; mtime change triggers update."""

import json
import os
import time
from datetime import UTC, datetime

from context_render.config import Config
from context_render.inventory.scanner import scan_components, write_manifest
from context_render.pipeline import parse_since, scan_repo
from context_render.store import Store
from context_render.store.db import FACTS_CID, STALE_CID

# ---- parse_since: --since is documented as local timezone; explicit offsets must win ----

def test_parse_since_bare_date_is_local_midnight():
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Taipei"  # UTC+8, no DST
    time.tzset()
    try:
        assert parse_since("2026-06-01") == datetime(2026, 5, 31, 16, 0, tzinfo=UTC)
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def test_parse_since_explicit_offset_preserved():
    dt = parse_since("2026-06-01T08:00:00+08:00")
    assert dt == datetime(2026, 6, 1, 0, 0, tzinfo=UTC)


def test_parse_since_relative_days():
    dt = parse_since("30d")
    assert 29 <= (datetime.now(UTC) - dt).days <= 30


def _prep(fake_repo):
    comps = [c for c in scan_components(fake_repo) if c.provenance == "local"]
    write_manifest(fake_repo, comps)


def _store(fake_repo):
    return Store(fake_repo / ".context-render" / "db.sqlite")


def test_scan_idempotent(fake_repo, fake_projects):
    _prep(fake_repo)
    cfg = Config()
    s1 = scan_repo(fake_repo, cfg, projects_dir=fake_projects)
    assert s1.new == 1
    st = _store(fake_repo)
    snap1 = st.snapshot()
    st.close()

    s2 = scan_repo(fake_repo, cfg, projects_dir=fake_projects)
    assert s2.skipped == 1 and s2.new == 0
    s3 = scan_repo(fake_repo, cfg, force=True, projects_dir=fake_projects)
    assert s3.updated == 1
    st = _store(fake_repo)
    snap3 = st.snapshot()
    st.close()
    assert snap1 == snap3  # re-runs don't double-count


def test_mtime_change_triggers_update(fake_repo, fake_projects):
    _prep(fake_repo)
    cfg = Config()
    scan_repo(fake_repo, cfg, projects_dir=fake_projects)
    f = next(fake_projects.rglob("*.jsonl"))
    os.utime(f, (f.stat().st_atime, f.stat().st_mtime + 100))
    s = scan_repo(fake_repo, cfg, projects_dir=fake_projects)
    assert s.updated == 1


def test_internal_rows_written(fake_repo, fake_projects):
    _prep(fake_repo)
    scan_repo(fake_repo, Config(), projects_dir=fake_projects)
    st = _store(fake_repo)
    rows = st.conn.execute(
        "SELECT component_id FROM usages WHERE component_id LIKE '\\_%' ESCAPE '\\'"
    ).fetchall()
    st.close()
    ids = {r["component_id"] for r in rows}
    assert "_event:git_commit" in ids
    assert "_cost:static" in ids
    assert "_facts:extract" in ids  # facts-extraction marker (coverage + backfill trigger)


def test_no_timestamp_session_visible_in_windowed_query(fake_repo, fake_projects):
    """no parseable event timestamp → started_at falls back to file mtime instead of NULL,
    so the session does not vanish from windowed views (started_at >= ?)."""
    import json

    _prep(fake_repo)
    src = next(fake_projects.rglob("*.jsonl"))
    lines = []
    for ln in src.read_text(encoding="utf-8").splitlines():
        obj = json.loads(ln)
        obj.pop("timestamp", None)
        lines.append(json.dumps(obj, ensure_ascii=False))
    (src.parent / "44444444-aaaa-bbbb-cccc-000000000004.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    scan_repo(fake_repo, Config(), projects_dir=fake_projects)
    st = _store(fake_repo)
    row = st.conn.execute(
        "SELECT started_at FROM sessions WHERE id LIKE '44444444%'"
    ).fetchone()
    windowed = st.sessions_since("2000-01-01T00:00:00+00:00")
    st.close()
    assert row["started_at"] is not None
    assert any(r["id"].startswith("44444444") for r in windowed)


def test_one_bad_session_degrades_not_aborts(fake_repo, fake_projects, monkeypatch):
    """Regression: an exception while ingesting one session must be counted as failed and
    skipped, not abort the scan — every session after it would otherwise never land."""
    from context_render import pipeline
    from tests.conftest import make_transcript, user_text

    proj_dir = next(fake_projects.iterdir())
    (proj_dir / "22222222-aaaa-bbbb-cccc-000000000002.jsonl").write_text(
        make_transcript(fake_repo, [user_text(0, str(fake_repo), "hi", sessionId="s2")]),
        encoding="utf-8",
    )
    real_attribute = pipeline.attribute

    def boom(parsed, components, repo_root):
        if parsed.session_id.startswith("22222222"):  # session_id = transcript filename stem
            raise RuntimeError("synthetic attribution crash")
        return real_attribute(parsed, components, repo_root)

    monkeypatch.setattr(pipeline, "attribute", boom)
    _prep(fake_repo)
    s = scan_repo(fake_repo, Config(), projects_dir=fake_projects)
    assert s.failed == 1
    assert s.new == 1  # the healthy session still lands
    assert "1 failed" in s.line()
    st = _store(fake_repo)
    ids = {r["id"] for r in st.conn.execute("SELECT id FROM sessions").fetchall()}
    st.close()
    assert len(ids) == 1  # nothing partial from the bad session
    assert not any(i.startswith("22222222") for i in ids)


def test_session_row_fields(fake_repo, fake_projects):
    _prep(fake_repo)
    scan_repo(fake_repo, Config(), projects_dir=fake_projects)
    st = _store(fake_repo)
    row = st.conn.execute("SELECT * FROM sessions").fetchone()
    st.close()
    assert row["project"] == fake_repo.name
    assert row["parse_status"] == "ok"
    assert row["turns"] >= 1
    assert row["cost_usd"] is not None  # USAGE fixture has usage + known model
    assert row["cc_version"] == "2.1.207"


def test_sidechain_files_scanned_and_freshness_tracked(fake_repo, fake_projects):
    """a subagent transcript appearing after the first ingest re-triggers the session
    (size covers the whole unit) and its activity lands in the stored evidence."""
    from tests.conftest import user_text, write_sidechain

    _prep(fake_repo)
    cfg = Config()
    scan_repo(fake_repo, cfg, projects_dir=fake_projects)
    main = next(fake_projects.rglob("*.jsonl"))
    write_sidechain(
        main, "a1",
        [user_text(2, str(fake_repo),
                   f"Base directory for this skill: {fake_repo}/.claude/skills/db-migrate")],
        agent_type="code-reviewer",
    )
    s = scan_repo(fake_repo, cfg, projects_dir=fake_projects)
    assert s.updated == 1 and s.skipped == 0
    st = _store(fake_repo)
    row = st.conn.execute(
        "SELECT evidence FROM usages WHERE component_id='skill:db-migrate' AND state='loaded'"
    ).fetchone()
    st.close()
    assert "[subagent:code-reviewer]" in row["evidence"]


def _minimal_session_row(sid="s1"):
    return {
        "id": sid, "project": "p", "path": "/gone.jsonl",
        "started_at": "2026-08-01T00:00:00+00:00", "ended_at": None,
        "turns": 1, "cost_usd": None, "prompt_digest": None, "cc_version": "2.1.207",
        "parse_status": "ok", "file_mtime": repr(1.0), "file_size": 10,
        "parsed_at": "2026-08-01T00:00:00+00:00",
    }


def _marker(sid, cid, extractor):
    return {"session_id": sid, "component_id": cid, "state": "invoked", "count": 0,
            "confidence": "heuristic",
            "evidence": json.dumps({"extractor": extractor})}


def test_stale_rows_roundtrip_and_marker_gate(tmp_path):
    from context_render.attributor.facts import FACTS_EXTRACTOR_VERSION
    from context_render.attributor.stale import STALE_EXTRACTOR_VERSION

    store = Store(tmp_path / "db.sqlite")
    try:
        stale = [{
            "session_id": "s1", "window_side": 0, "window_agent": "",
            "path": "a.md", "read_idx": 0, "mutate_idx": 2, "mutate_tool": "Edit",
            "close_idx": None, "outcome": "never-re-read",
            "read_tokens_est": 100, "read_partial": 0, "confidence": "exact",
        }]
        store.replace_session(
            _minimal_session_row(),
            [_marker("s1", FACTS_CID, FACTS_EXTRACTOR_VERSION),
             _marker("s1", STALE_CID, STALE_EXTRACTOR_VERSION)],
            [], stale)
        rows = store.stale_for_sessions(["s1"])
        assert len(rows) == 1 and rows[0]["path"] == "a.md"
        assert rows[0]["outcome"] == "never-re-read" and rows[0]["close_idx"] is None

        # both markers current → no update needed
        assert store.needs_update("s1", 1.0, 10, FACTS_EXTRACTOR_VERSION,
                                  STALE_EXTRACTOR_VERSION) is False
        # stale extractor bumped → stale again
        assert store.needs_update("s1", 1.0, 10, FACTS_EXTRACTOR_VERSION,
                                  STALE_EXTRACTOR_VERSION + 1) is True
        # stale check opted out (0) → old behavior
        assert store.needs_update("s1", 1.0, 10, FACTS_EXTRACTOR_VERSION) is False

        # idempotent replace: cascade clears stale rows too
        store.replace_session(_minimal_session_row(), [], [], [])
        assert store.stale_for_sessions(["s1"]) == []
    finally:
        store.close()


def test_scan_writes_stale_marker_and_stale_rows(fake_repo, fake_projects, tmp_path):
    """rich fixture has no stale pattern → 0 rows but the marker must land,
    and needs_update must go quiet afterwards (backfill semantics)."""
    from context_render.attributor.facts import FACTS_EXTRACTOR_VERSION
    from context_render.attributor.stale import STALE_EXTRACTOR_VERSION
    from context_render.config import Config
    from context_render.pipeline import scan_repo
    from context_render.store import STALE_CID, Store

    _prep(fake_repo)
    db = tmp_path / "db.sqlite"
    store = Store(db)
    try:
        scan_repo(fake_repo, Config(), store=store, projects_dir=fake_projects)
        sid = "11111111-aaaa-bbbb-cccc-000000000001"
        marker = store.conn.execute(
            "SELECT evidence FROM usages WHERE session_id=? AND component_id=?",
            (sid, STALE_CID)).fetchone()
        assert marker is not None
        import json
        assert json.loads(marker["evidence"])["extractor"] == STALE_EXTRACTOR_VERSION
        assert store.stale_for_sessions([sid]) == []
        row = store.conn.execute(
            "SELECT file_mtime, file_size FROM sessions WHERE id=?", (sid,)).fetchone()
        import ast
        assert store.needs_update(sid, ast.literal_eval(row["file_mtime"]),
                                  row["file_size"], FACTS_EXTRACTOR_VERSION,
                                  STALE_EXTRACTOR_VERSION) is False
    finally:
        store.close()


def test_build_rows_carries_stale_windows_through_replace(tmp_path, fake_repo):
    """A read → edit → never-re-read span reaches the stale_windows table in exactly the
    shape build_rows emits, and a repeated replace_session leaves the snapshot unchanged."""
    from context_render.attributor import attribute
    from context_render.parser import parse_file
    from context_render.parser.discovery import SessionFile
    from context_render.pipeline import build_rows
    from tests.conftest import USAGE, assistant, make_transcript, tool_result, tool_use

    cwd = str(fake_repo)
    target = f"{fake_repo}/docs/conventions.md"
    lines = [
        assistant(0, cwd, [tool_use("Read", {"file_path": target}, "t1")], usage=USAGE),
        tool_result(1, cwd, "t1", "conventions doc"),
        assistant(2, cwd, [tool_use("Edit", {"file_path": target, "old_string": "a",
                                             "new_string": "b"}, "t2")], usage=USAGE),
        tool_result(3, cwd, "t2", "ok"),
    ]
    p = tmp_path / "77777777-aaaa-bbbb-cccc-000000000007.jsonl"
    p.write_text(make_transcript(fake_repo, lines), encoding="utf-8")
    parsed = parse_file(p)
    comps = [c for c in scan_components(fake_repo) if c.provenance == "local"]
    sf = SessionFile(path=p, session_id=p.stem, mtime=p.stat().st_mtime, size=p.stat().st_size)
    srow, urows, frows, strows = build_rows(parsed, attribute(parsed, comps, fake_repo),
                                            comps, sf, fake_repo, Config())
    assert strows == [{
        "session_id": p.stem, "window_side": 0, "window_agent": "",
        "path": "docs/conventions.md", "read_idx": 0, "mutate_idx": 2, "mutate_tool": "Edit",
        "close_idx": None, "outcome": "never-re-read",
        "read_tokens_est": 4, "read_partial": 0, "confidence": "exact",
    }]
    marker = next(u for u in urows if u["component_id"] == STALE_CID)
    assert marker["count"] == 1 and json.loads(marker["evidence"])["stale_windows"] == 1
    store = Store(tmp_path / "db.sqlite")
    try:
        store.replace_session(srow, urows, frows, strows)
        assert [dict(r) for r in store.stale_for_sessions([p.stem])] == strows
        snap = store.snapshot()
        store.replace_session(srow, urows, frows, strows)
        assert store.snapshot() == snap
    finally:
        store.close()
