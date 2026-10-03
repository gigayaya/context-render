"""Error and degradation matrix walkthrough."""

import json

import pytest

from context_render.config import Config
from context_render.errors import PreconditionError
from context_render.inventory.scanner import scan_components, write_manifest
from context_render.pipeline import scan_repo
from context_render.store import Store
from tests.conftest import make_transcript


def _prep(fake_repo):
    comps = [c for c in scan_components(fake_repo) if c.provenance == "local"]
    write_manifest(fake_repo, comps)


def test_no_transcript_precondition(fake_repo, tmp_path):
    _prep(fake_repo)
    empty = tmp_path / "empty-projects"
    empty.mkdir()
    with pytest.raises(PreconditionError):
        scan_repo(fake_repo, Config(), projects_dir=empty)


def test_unsupported_version_all_heuristic(fake_repo, fake_projects, capsys):
    _prep(fake_repo)
    # tamper the version to an unsupported one
    f = next(fake_projects.rglob("*.jsonl"))
    lines = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()]
    for obj in lines:
        obj["version"] = "9.9.9"
    f.write_text(make_transcript(fake_repo, lines), encoding="utf-8")
    scan_repo(fake_repo, Config(), projects_dir=fake_projects)
    assert "not in the support matrix" in capsys.readouterr().err
    store = Store(fake_repo / ".context-render" / "db.sqlite")
    try:
        rows = store.conn.execute(
            "SELECT DISTINCT confidence FROM usages WHERE component_id NOT LIKE '\\_%' ESCAPE '\\'"
        ).fetchall()
    finally:
        store.close()
    assert {r["confidence"] for r in rows} == {"heuristic"}


def test_db_schema_mismatch(fake_repo):
    db = fake_repo / ".context-render" / "db.sqlite"
    store = Store(db)
    store.conn.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
    store.conn.commit()
    store.close()
    with pytest.raises(PreconditionError, match="schema"):
        Store(db)


def test_degraded_summary_counted(fake_repo, fake_projects, capsys):
    _prep(fake_repo)
    f = next(fake_projects.rglob("*.jsonl"))
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "future-unknown-type", "cwd": str(fake_repo)}) + "\n")
    summary = scan_repo(fake_repo, Config(), projects_dir=fake_projects)
    assert summary.degraded == 1
    assert "degraded" in capsys.readouterr().err
