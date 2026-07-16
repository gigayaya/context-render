"""analyze command + self-derivation aggregation/rendering (SPIKES.md W3)."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from context_render.attributor import attribute
from context_render.attributor.facts import extract_facts
from context_render.cli import app
from context_render.config import Config
from context_render.inventory.scanner import scan_components, write_manifest
from context_render.parser import parse_file
from context_render.pipeline import scan_repo
from context_render.report.aggregate import aggregate_session
from context_render.report.render_md import render_md
from context_render.report.render_term import analyze_lines, session_selfderive_lines
from context_render.report.selfderive import aggregate_analyze, aggregate_rows, select_row
from context_render.report.ansi import Style
from context_render.store import FACTS_CID, Store
from tests.conftest import USAGE, assistant, make_transcript, tool_result, tool_use, user_text

runner = CliRunner()


def search_lines(repo):
    """A search-heavy session: repo-layout mapping, two pattern variants, one chain read."""
    cwd = str(repo)
    return [
        user_text(0, cwd, "find the retry policy"),
        assistant(1, cwd, [tool_use("Bash", {"command": "find . -type d"}, "t1")], usage=USAGE),
        tool_result(2, cwd, "t1", "./src\n./tests\n" * 6),
        assistant(3, cwd, [tool_use("Bash", {"command": "grep -rn retry_policy src"}, "t2")],
                  usage=USAGE),
        tool_result(4, cwd, "t2", "src/retry.py:10: retry_policy = ..."),
        assistant(5, cwd, [tool_use("Read", {"file_path": f"{repo}/src/retry.py"}, "t3")],
                  usage=USAGE),
        tool_result(6, cwd, "t3", "def retry(): ..." * 8),
        assistant(7, cwd, [tool_use("Bash", {"command": "grep -rn retryPolicy src"}, "t4")],
                  usage=USAGE),
        tool_result(8, cwd, "t4", "src/retry.py:22: retryPolicy"),
    ]


def _prep(fake_repo):
    comps = [c for c in scan_components(fake_repo) if c.provenance == "local"]
    write_manifest(fake_repo, comps)


def _store(fake_repo):
    return Store(fake_repo / ".context-render" / "db.sqlite")


def _add_session(fake_projects, fake_repo, sid, lines):
    proj_dir = next(fake_projects.iterdir())
    (proj_dir / f"{sid}.jsonl").write_text(
        make_transcript(fake_repo, lines), encoding="utf-8")


# ---- aggregation ----

def _fact(key="retrypolicy", kind="search", raw="retry_policy", tool="grep", tokens=100,
          occ=200, sid="s1", conf="exact", idx=0):
    return {"session_id": sid, "idx": idx, "kind": kind, "key": key, "raw": raw,
            "tool": tool, "tokens_est": tokens, "occupancy_est": occ, "sidechain": 0,
            "confidence": conf}


def test_aggregate_rows_grouping_and_method():
    rows = aggregate_rows([
        _fact(idx=0, raw="retry_policy"),
        _fact(idx=1, raw="retryPolicy", sid="s2"),
        _fact(idx=2, kind="chain_read", raw="/x/a.py", tool="Read", conf="heuristic"),
        _fact(idx=3, key="repo layout", kind="mapping", raw="find . -type d", tool="Bash",
              tokens=10, occ=None),
    ])
    assert [r["key"] for r in rows] == ["retrypolicy", "repo layout"]  # tokens desc
    kw = rows[0]
    assert kw["sessions"] == 2 and kw["times"] == 3
    assert kw["tokens"] == 300 and kw["occupancy"] == 600
    assert kw["label"] == "retry_policy"  # most common raw, not the folded canonical
    assert "2 variants tried" in kw["method"]
    assert "+read chains" in kw["method"]
    assert kw["method"].endswith("~")  # chain read is heuristic
    mp = rows[1]
    assert mp["label"] == "repo layout"
    assert mp["method"] == "find -type d"
    assert mp["occupancy"] is None  # not computable stays empty, never guessed


def test_aggregate_rows_occupancy_partial_sum():
    rows = aggregate_rows([_fact(idx=0, occ=None), _fact(idx=1, occ=50)])
    assert rows[0]["occupancy"] == 50


# ---- end-to-end through store ----

def test_analyze_end_to_end(fake_repo, fake_projects):
    _prep(fake_repo)
    _add_session(fake_projects, fake_repo, "33333333-aaaa-bbbb-cccc-000000000003",
                 search_lines(fake_repo))
    scan_repo(fake_repo, Config(), projects_dir=fake_projects)
    store = _store(fake_repo)
    try:
        agg, fact_rows = aggregate_analyze(store, Config(), since_iso=None,
                                           since_label="all history")
    finally:
        store.close()
    assert agg["report_type"] == "analyze"
    assert agg["window"]["session_count"] == 2  # rich fixture + search session
    assert agg["window"]["facts_sessions"] == 2
    keys = [r["key"] for r in agg["rows"]]
    assert "retrypolicy" in keys and "repo layout" in keys
    kw = next(r for r in agg["rows"] if r["key"] == "retrypolicy")
    assert kw["sessions"] == 1
    assert "2 variants tried" in kw["method"]
    assert "+read chains" in kw["method"]
    assert agg["summary"]["tokens"] == sum(r["tokens"] for r in agg["rows"])
    assert agg["summary"]["pct"] is not None
    assert any(f["key"] == "retrypolicy" for f in fact_rows)

    body = "\n".join(analyze_lines(agg, Config(), full=False))
    assert "Self-derivation cost — all history, 2 sessions (facts: 2 of 2)" in body
    assert "information the harness didn't provide" in body
    assert "repo layout" in body
    # md and terminal share the line builders
    md = render_md(agg, Config())
    for line in analyze_lines(agg, Config(), full=False):
        assert line.rstrip() in md


def test_prefacts_session_backfilled_by_plain_sync(fake_repo, fake_projects):
    """A session ingested before the facts feature reads as stale (marker missing) and a
    plain sync rebuilds it while the transcript still exists — no --force needed."""
    _prep(fake_repo)
    cfg = Config()
    scan_repo(fake_repo, cfg, projects_dir=fake_projects)
    store = _store(fake_repo)
    store.conn.execute("DELETE FROM facts")
    store.conn.execute("DELETE FROM usages WHERE component_id=?", (FACTS_CID,))
    store.conn.commit()
    store.close()

    s = scan_repo(fake_repo, cfg, projects_dir=fake_projects)
    assert s.updated == 1 and s.skipped == 0
    store = _store(fake_repo)
    try:
        marker = store.conn.execute(
            "SELECT count, evidence FROM usages WHERE component_id=?", (FACTS_CID,)
        ).fetchone()
        assert marker is not None
        assert json.loads(marker["evidence"])["tool_output_tokens_est"] > 0
    finally:
        store.close()


def test_analyze_counts_uncovered_sessions(fake_repo, fake_projects):
    """A marker-less session (expired before extraction) counts in the window but not in
    the facts coverage — 'facts: N of M' is honest about it."""
    _prep(fake_repo)
    scan_repo(fake_repo, Config(), projects_dir=fake_projects)
    store = _store(fake_repo)
    try:
        store.conn.execute("DELETE FROM facts")
        store.conn.execute("DELETE FROM usages WHERE component_id=?", (FACTS_CID,))
        store.conn.commit()
        agg, _ = aggregate_analyze(store, Config(), since_iso=None, since_label="all history")
    finally:
        store.close()
    assert agg["window"]["session_count"] == 1
    assert agg["window"]["facts_sessions"] == 0
    assert any("no session in this window has extracted facts" in w for w in agg["warnings"])


# ---- CLI ----

def test_analyze_cli(fake_repo, fake_projects, monkeypatch):
    monkeypatch.chdir(fake_repo)
    monkeypatch.setenv("CONTEXT_RENDER_PROJECTS_DIR", str(fake_projects))
    _add_session(fake_projects, fake_repo, "33333333-aaaa-bbbb-cccc-000000000003",
                 search_lines(fake_repo))
    runner.invoke(app, ["init", "--yes", "--no-hook"])
    assert runner.invoke(app, ["sync"]).exit_code == 0

    r = runner.invoke(app, ["analyze", "--since", "12w"])
    assert r.exit_code == 0, r.output
    assert "Self-derivation cost — last 12w" in r.output
    assert "repo layout" in r.output

    r_md = runner.invoke(app, ["analyze", "--since", "12w", "--md"])
    assert r_md.exit_code == 0
    reports = list((fake_repo / ".context-render" / "reports").glob("analyze-*.md"))
    assert len(reports) == 1

    # emit-prompt by canonical key (row numbers also accepted but unstable across runs)
    r_p = runner.invoke(app, ["analyze", "--since", "12w", "--emit-prompt", "repo layout"])
    assert r_p.exit_code == 0, r_p.output
    assert "what the agent was after: repo layout" in r_p.output
    assert "find . -type d" in r_p.output
    assert "does not" in r_p.output  # no scaffold-form recommendation

    r_n = runner.invoke(app, ["analyze", "--since", "12w", "--emit-prompt", "1"])
    assert r_n.exit_code == 0
    assert "evidence (session" in r_n.output

    r_bad = runner.invoke(app, ["analyze", "--emit-prompt", "no-such-key"])
    assert r_bad.exit_code == 3

    # help text lists the command
    assert "analyze" in runner.invoke(app, ["help"]).output


def test_analyze_cli_without_manifest(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    assert runner.invoke(app, ["analyze"]).exit_code == 3


# ---- session report block (§3.2) ----

def test_session_report_selfderive_block(fake_repo, fake_projects, monkeypatch):
    monkeypatch.chdir(fake_repo)
    monkeypatch.setenv("CONTEXT_RENDER_PROJECTS_DIR", str(fake_projects))
    _add_session(fake_projects, fake_repo, "33333333-aaaa-bbbb-cccc-000000000003",
                 search_lines(fake_repo))
    runner.invoke(app, ["init", "--yes", "--no-hook"])
    r = runner.invoke(app, ["sessions", "33333333"])
    assert r.exit_code == 0, r.output
    assert "SELF-DERIVATION" in r.output
    assert "repo layout" in r.output


def test_selfderive_block_top5_and_full(tmp_path, fake_repo):
    cwd = str(fake_repo)
    lines = []
    for i in range(7):
        tid = f"t{i}"
        lines.append(assistant(i * 2, cwd,
                               [tool_use("Grep", {"pattern": f"needle_{i}"}, tid)]))
        lines.append(tool_result(i * 2 + 1, cwd, tid, "x" * 40))
    p = tmp_path / "s.jsonl"
    p.write_text(make_transcript(fake_repo, lines), encoding="utf-8")
    parsed = parse_file(p)
    comps = [c for c in scan_components(fake_repo) if c.provenance == "local"]
    att = attribute(parsed, comps, fake_repo)
    agg = aggregate_session(parsed, att, comps, Config(),
                            facts=extract_facts(parsed).facts)
    assert len(agg["self_derivation"]) == 7
    short = "\n".join(session_selfderive_lines(agg, full=False, style=Style()))
    assert "SELF-DERIVATION — top 5 of 7 (--full for all)" in short
    assert short.count("needle_") == 5
    full = "\n".join(session_selfderive_lines(agg, full=True, style=Style()))
    assert full.count("needle_") == 7


def test_selfderive_block_unavailable_and_empty():
    unavailable = session_selfderive_lines({"self_derivation": None}, False, Style())
    assert any("transcript expired before facts extraction" in ln for ln in unavailable)
    assert session_selfderive_lines({"self_derivation": []}, False, Style()) == []


def test_select_row_by_number_and_key():
    rows = [{"key": "repo layout", "label": "repo layout"},
            {"key": "retrypolicy", "label": "retry_policy"}]
    assert select_row(rows, "2")["key"] == "retrypolicy"
    assert select_row(rows, "repo layout")["key"] == "repo layout"
    assert select_row(rows, "retry_policy")["key"] == "retrypolicy"  # label matches too
    assert select_row(rows, "9") is None
    assert select_row(rows, "nope") is None
