"""ctxr live: read-only JSON snapshot for the ctxr-live mod."""

import json
import os
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from context_render.cli import app
from context_render.report.live import LIVE_SCHEMA_VERSION, NO_MANIFEST_NOTICE, live_snapshot
from tests.conftest import make_transcript, user_text
from tests.test_report import _session_agg

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parent.parent
SID = "11111111"


def _init(fake_repo, fake_projects, monkeypatch):
    monkeypatch.chdir(fake_repo)
    monkeypatch.setenv("CONTEXT_RENDER_PROJECTS_DIR", str(fake_projects))
    assert runner.invoke(app, ["init", "--yes", "--no-hook"]).exit_code == 0


def _transcript(fake_projects) -> Path:
    return next(fake_projects.rglob(f"{SID}*.jsonl"))


def test_live_snapshot_shape(tmp_path, fake_repo, rich_session_lines):
    agg = _session_agg(tmp_path, fake_repo, rich_session_lines)
    snap = live_snapshot(agg, width=40, window_tokens=200_000)
    assert snap["schema_version"] == LIVE_SCHEMA_VERSION == 4
    assert snap["vertical"] is None  # no height: no sidebar map
    assert snap["width"] == 40
    assert [e["no"] for e in snap["timeline"]] == list(range(1, len(snap["timeline"]) + 1))
    assert all(isinstance(e["sidechain"], bool) for e in snap["timeline"])
    assert snap["timeline"][0]["kind"] == "session_start"
    assert snap["map"] and all(set(s) == {"t", "role", "no"} for r in snap["map"] for s in r)
    by_no = {e["no"]: e for e in snap["timeline"]}
    for row in snap["map"]:
        for s in row:
            if s["no"] is not None:
                assert by_no[s["no"]]["sidechain"] is False
    # USAGE fixture: 10k prompt tokens per sampled turn vs the 200k window
    assert snap["occupancy"] == {"current": 10_000, "peak": 10_000, "window": 200_000}
    assert isinstance(snap["warnings"], list)
    assert snap["notice"] is None
    json.dumps(snap)  # serializable as-is


def test_live_map_width_respected(tmp_path, fake_repo, rich_session_lines):
    agg = _session_agg(tmp_path, fake_repo, rich_session_lines)
    for width in (20, 40, 100):
        snap = live_snapshot(agg, width=width, window_tokens=200_000)
        box = next(r for r in snap["map"] if any(s["t"].startswith("┌") for s in r))
        assert "".join(s["t"] for s in box).strip() == "┌" + "─" * width + "┐"
        for row in snap["map"]:
            for s in row:
                if s["no"] is not None:  # labels never cross the right border
                    start = len("".join(x["t"] for x in row[: row.index(s)]))
                    assert start + len(s["t"]) <= 11 + width


def test_live_cli_json(fake_repo, fake_projects, monkeypatch):
    _init(fake_repo, fake_projects, monkeypatch)
    r = runner.invoke(app, ["live", SID, "--json", "--width", "40"])
    assert r.exit_code == 0, r.output
    snap = json.loads(r.output)
    assert snap["session_id"].startswith(SID)
    assert snap["width"] == 40


def test_live_without_init_scans_in_memory(fake_repo, fake_projects, monkeypatch):
    """No manifest: live scans the scaffolds in memory (same components a fresh init would
    write), says so in `notice`, and creates nothing under .context-render/."""
    monkeypatch.chdir(fake_repo)
    monkeypatch.setenv("CONTEXT_RENDER_PROJECTS_DIR", str(fake_projects))
    r = runner.invoke(app, ["live", SID, "--json"])
    assert r.exit_code == 0, r.output
    bare = json.loads(r.output)
    assert bare["notice"] == NO_MANIFEST_NOTICE
    assert not (fake_repo / ".context-render").exists()

    assert runner.invoke(app, ["init", "--yes", "--no-hook"]).exit_code == 0
    inited = json.loads(runner.invoke(app, ["live", SID, "--json"]).output)
    assert inited["notice"] is None
    assert {**bare, "notice": None} == inited


def test_live_serves_a_just_written_session(fake_repo, fake_projects, monkeypatch):
    """The fixture transcript was just written (as an in-progress one is): live serves it
    without any prior sync."""
    _init(fake_repo, fake_projects, monkeypatch)
    _transcript(fake_projects).touch()
    assert runner.invoke(app, ["live", SID, "--json"]).exit_code == 0


def test_live_never_touches_db(fake_repo, fake_projects, monkeypatch):
    _init(fake_repo, fake_projects, monkeypatch)
    db = fake_repo / ".context-render" / "db.sqlite"
    if db.exists():  # whether init creates it is not this test's business
        db.unlink()
    assert runner.invoke(app, ["live", SID, "--json"]).exit_code == 0
    assert not db.exists()
    assert runner.invoke(app, ["sync"]).exit_code == 0
    before = (db.stat().st_mtime_ns, db.read_bytes())
    assert runner.invoke(app, ["live", SID, "--json"]).exit_code == 0
    assert (db.stat().st_mtime_ns, db.read_bytes()) == before


def test_live_errors(fake_repo, fake_projects, monkeypatch):
    _init(fake_repo, fake_projects, monkeypatch)
    assert runner.invoke(app, ["live", "deadbeef", "--json"]).exit_code == 3
    assert runner.invoke(app, ["live", SID]).exit_code == 2  # --json is required
    assert runner.invoke(app, ["live", SID, "--json", "--width", "19"]).exit_code == 2
    assert runner.invoke(app, ["live", SID, "--json", "--width", "201"]).exit_code == 2


def test_live_tolerates_truncated_last_line(fake_repo, fake_projects, monkeypatch):
    """A refresh can land while Claude Code is mid-write on the transcript's last line."""
    _init(fake_repo, fake_projects, monkeypatch)
    path = _transcript(fake_projects)
    path.write_text(path.read_text(encoding="utf-8") + '{"type": "assistant", "mess',
                    encoding="utf-8")
    r = runner.invoke(app, ["live", SID, "--json"])
    assert r.exit_code == 0, r.output
    json.loads(r.output)


def test_live_fresh_session_has_valid_json(fake_repo, fake_projects, monkeypatch):
    """Before any load/action the map may be empty; the snapshot stays well-formed."""
    _init(fake_repo, fake_projects, monkeypatch)
    _transcript(fake_projects).write_text(
        make_transcript(fake_repo, [user_text(0, str(fake_repo), "hi")]), encoding="utf-8")
    r = runner.invoke(app, ["live", SID, "--json"])
    assert r.exit_code == 0, r.output
    snap = json.loads(r.output)
    assert isinstance(snap["map"], list) and isinstance(snap["timeline"], list)


def test_live_imports_no_network_modules(fake_repo, fake_projects, monkeypatch):
    _init(fake_repo, fake_projects, monkeypatch)
    code = (
        "import sys\n"
        "from context_render.cli import app\n"
        "try:\n"
        f"    app(['live', '{SID}', '--json'])\n"
        "except SystemExit as e:\n"
        "    assert e.code in (0, None), e.code\n"
        "bad = [m for m in ('socket', 'ssl', 'http.client', 'urllib.request')"
        " if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT),
           "CONTEXT_RENDER_PROJECTS_DIR": str(fake_projects)}
    proc = subprocess.run([sys.executable, "-c", code], cwd=fake_repo, env=env,
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


def test_live_snapshot_uses_scroll_layout(tmp_path, fake_repo, rich_session_lines):
    from context_render.report.context_map import context_map_parts, last_marks

    agg = _session_agg(tmp_path, fake_repo, rich_session_lines)
    snap = live_snapshot(agg, width=40, window_tokens=200_000)
    rows, axis = context_map_parts(agg["timeline"], agg["context_samples"], width=40,
                                   window_tokens=200_000, layout="scroll")
    assert snap["map"] == rows and snap["axis"] == axis
    assert snap["last"] == last_marks(agg["timeline"], agg["context_samples"], width=40,
                                      layout="scroll")
    assert snap["last"]["load"] is not None and snap["last"]["action"] is not None


def test_live_snapshot_vertical_with_height(tmp_path, fake_repo, rich_session_lines):
    from context_render.report.context_map import context_map_vertical

    agg = _session_agg(tmp_path, fake_repo, rich_session_lines)
    snap = live_snapshot(agg, width=40, window_tokens=200_000, height=20)
    assert snap["vertical"] == context_map_vertical(agg["timeline"], agg["context_samples"], 20)
    assert snap["vertical"] is not None
    assert len(snap["vertical"]["rows"]) == len(snap["vertical"]["row_no"])
    json.dumps(snap)


def test_live_cli_height(fake_repo, fake_projects, monkeypatch):
    _init(fake_repo, fake_projects, monkeypatch)
    plain = json.loads(runner.invoke(app, ["live", SID, "--json"]).output)
    assert plain["vertical"] is None
    r = runner.invoke(app, ["live", SID, "--json", "--height", "20"])
    assert r.exit_code == 0, r.output
    snap = json.loads(r.output)
    assert snap["vertical"]["rows"] and snap["map"] == plain["map"]
    assert runner.invoke(app, ["live", SID, "--json", "--height", "0"]).exit_code == 2
    assert runner.invoke(app, ["live", SID, "--json", "--height", "201"]).exit_code == 2
