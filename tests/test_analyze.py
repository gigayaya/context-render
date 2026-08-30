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
from context_render.report.render_term import (
    _selfderive_header,
    _selfderive_row,
    analyze_lines,
    session_selfderive_lines,
)
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
    assert "tried 2 spellings" in kw["method"]
    assert "opened hits" in kw["method"]
    assert kw["method"].endswith("~")  # chain read is heuristic
    mp = rows[1]
    assert mp["label"] == "repo layout"
    assert mp["method"] == "find -type d"
    assert mp["occupancy"] is None  # not computable stays empty, never guessed


def test_aggregate_rows_occupancy_partial_sum():
    rows = aggregate_rows([_fact(idx=0, occ=None), _fact(idx=1, occ=50)])
    assert rows[0]["occupancy"] == 50


def _fact_row(sid, idx, kind, key, raw, tool, tokens, conf="exact"):
    return _fact(key=key, kind=kind, raw=raw, tool=tool, tokens=tokens,
                 occ=None, sid=sid, conf=conf, idx=idx)


def test_code_structure_row_method_shows_probe_shapes():
    from context_render.attributor.facts import CODE_KEY
    rows = aggregate_rows([
        _fact_row("s1", 1, "mapping", CODE_KEY, "def ", "grep", 100, conf="exact"),
        _fact_row("s1", 3, "mapping", CODE_KEY, "def ", "grep", 80, conf="exact"),
        _fact_row("s1", 5, "mapping", CODE_KEY, "len(", "grep", 60, conf="heuristic"),
        _fact_row("s1", 7, "mapping", CODE_KEY, "*.py", "find", 40, conf="exact"),
        _fact_row("s1", 9, "mapping", CODE_KEY, "tree", "Bash", 20, conf="exact"),
        _fact_row("s2", 2, "mapping", "repo layout", "find . -type d", "Bash", 50,
                  conf="exact"),
    ])
    cs = next(r for r in rows if r["key"] == CODE_KEY)
    assert cs["kind"] == "action" and cs["label"] == CODE_KEY
    assert cs["method"].startswith("probes: def , len(, *.py +1")   # top-3 + 溢出
    assert "spellings" not in cs["method"]
    assert cs["heuristic"] is True                          # 任一 heuristic 感染整列,現行規則
    rl = next(r for r in rows if r["key"] == "repo layout")
    assert rl is not cs                                     # 兩個 action 列不合併


def test_aggregate_rows_story_counts_searches_and_rereads():
    rows = aggregate_rows([
        _fact(idx=0, raw="retry_policy"),
        _fact(idx=2, raw="retry_policy"),
        _fact(idx=4, raw="retryPolicy"),
        _fact(idx=6, kind="chain_read", raw="/x/src/timeline.py", tool="Read",
              conf="heuristic"),
        _fact(idx=8, kind="chain_read", raw="/x/src/timeline.py", tool="Read",
              conf="heuristic"),
        _fact(idx=9, kind="chain_read", raw="/x/src/cli.py", tool="Read",
              conf="heuristic"),
    ])
    story = rows[0]["story"]
    assert story["searches"][0] == ("grep", "retry_policy", 2)  # 最頻搜法在前
    assert ("grep", "retryPolicy", 1) in story["searches"]
    assert story["reads"] == [("timeline.py", 2), ("cli.py", 1)]  # basename、重讀計數


def test_aggregate_rows_story_shapes_for_action_rows():
    from context_render.attributor.facts import CODE_KEY
    rows = aggregate_rows([
        _fact_row("s1", 1, "mapping", CODE_KEY, "def ", "grep", 100),
        _fact_row("s1", 3, "mapping", "repo layout", "find . -type d -maxdepth 2",
                  "Bash", 50),
        _fact_row("s1", 5, "search", "needle", "needle", "Grep", 10),
    ])
    cs = next(r for r in rows if r["key"] == CODE_KEY)
    assert cs["story"]["searches"] == [("grep", "def ", 1)]
    rl = next(r for r in rows if r["key"] == "repo layout")
    assert rl["story"]["searches"] == [("find -type d", "", 1)]  # raw 空 → 渲染只印 tool
    kw = next(r for r in rows if r["key"] == "needle")
    assert kw["story"]["reads"] == []  # 無 chain-read 的列 reads 為空


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
    assert "tried 2 spellings" in kw["method"]
    assert "opened hits" in kw["method"]
    assert agg["summary"]["tokens"] == sum(r["tokens"] for r in agg["rows"])
    assert agg["summary"]["pct"] is not None
    assert any(f["key"] == "retrypolicy" for f in fact_rows)

    body = "\n".join(analyze_lines(agg, Config(), full=False))
    assert "Self-derivation cost — all history, 2 sessions (facts: 2 of 2)" in body
    assert "questions the harness didn't answer" in body
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


def test_stale_extractor_version_triggers_reingest(fake_repo, fake_projects):
    """A DB built by an older extractor (marker without the extractor field = W3 = v1)
    reads as stale even though the transcript file is unchanged; a plain sync re-extracts
    and stamps the current version."""
    from context_render.attributor.facts import FACTS_EXTRACTOR_VERSION

    _prep(fake_repo)
    cfg = Config()
    scan_repo(fake_repo, cfg, projects_dir=fake_projects)
    store = _store(fake_repo)
    marker = store.conn.execute(
        "SELECT evidence FROM usages WHERE component_id=?", (FACTS_CID,)).fetchone()
    ev = json.loads(marker["evidence"])
    assert ev["extractor"] == FACTS_EXTRACTOR_VERSION
    del ev["extractor"]  # simulate a W3-build marker
    store.conn.execute(
        "UPDATE usages SET evidence=? WHERE component_id=?",
        (json.dumps(ev), FACTS_CID))
    store.conn.commit()
    store.close()

    s = scan_repo(fake_repo, cfg, projects_dir=fake_projects)
    assert s.updated == 1 and s.skipped == 0
    store = _store(fake_repo)
    try:
        marker = store.conn.execute(
            "SELECT evidence FROM usages WHERE component_id=?", (FACTS_CID,)).fetchone()
        assert json.loads(marker["evidence"])["extractor"] == FACTS_EXTRACTOR_VERSION
    finally:
        store.close()


def test_current_extractor_version_skips(fake_repo, fake_projects):
    """Same version + unchanged file → the second sync doesn't re-read the transcript."""
    _prep(fake_repo)
    cfg = Config()
    scan_repo(fake_repo, cfg, projects_dir=fake_projects)
    s = scan_repo(fake_repo, cfg, projects_dir=fake_projects)
    assert s.skipped == 1 and s.updated == 0 and s.new == 0


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
    assert short.count("needle_") == 10  # 5 列,每列一行 + 一句實例句
    full = "\n".join(session_selfderive_lines(agg, full=True, style=Style()))
    assert full.count("needle_") == 12   # 7 列,實例句僅 top-5


def test_session_selfderive_summary_pct(tmp_path, fake_repo):
    cwd = str(fake_repo)
    lines = [
        assistant(0, cwd, [tool_use("Grep", {"pattern": "needle_x"}, "t1")]),
        tool_result(1, cwd, "t1", "x" * 400),   # 100 tok 搜尋結果
        assistant(2, cwd, [tool_use("Bash", {"command": "echo hi"}, "t2")]),
        tool_result(3, cwd, "t2", "y" * 1200),  # 300 tok 非搜尋輸出 → 分母 400
    ]
    p = tmp_path / "s.jsonl"
    p.write_text(make_transcript(fake_repo, lines), encoding="utf-8")
    parsed = parse_file(p)
    comps = [c for c in scan_components(fake_repo) if c.provenance == "local"]
    att = attribute(parsed, comps, fake_repo)
    ext = extract_facts(parsed)
    agg = aggregate_session(parsed, att, comps, Config(), facts=ext.facts,
                            facts_tool_output=ext.tool_output_tokens_est)
    s = agg["self_derivation_summary"]
    assert s["tokens"] == 100
    assert s["pct"] == 25.0

    agg2 = aggregate_session(parsed, att, comps, Config(), facts=ext.facts)
    assert agg2["self_derivation_summary"]["pct"] is None  # 分母缺 → 不猜

    agg3 = aggregate_session(parsed, att, comps, Config())
    assert agg3["self_derivation_summary"] is None  # facts 從未萃取


def test_selfderive_block_unavailable_and_empty():
    unavailable = session_selfderive_lines({"self_derivation": None}, False, Style())
    assert any("transcript expired before facts extraction" in ln for ln in unavailable)
    assert session_selfderive_lines({"self_derivation": []}, False, Style()) == []


def test_selfderive_row_quotes_and_how_header():
    hdr = _selfderive_header(Style(), with_sessions=False)
    assert "what the agent was after" in hdr and "how" in hdr
    kw = {"kind": "keyword", "label": "fromisoformat", "method": "grep, opened hits ~",
          "times": 12, "tokens": 28500, "occupancy": 5_100_000, "story": {}}
    act = {"kind": "action", "label": "code structure", "method": "probes: def",
           "times": 10, "tokens": 27800, "occupancy": None, "story": {}}
    assert "'fromisoformat'" in _selfderive_row(1, kw, Style(), with_sessions=False)
    assert "'code structure'" not in _selfderive_row(2, act, Style(), with_sessions=False)


def test_story_line_shapes():
    from context_render.report.render_term import _story_line
    full = {"kind": "keyword", "story": {
        "searches": [("grep", "fromisoformat", 2), ("rg", "fromiso", 1)],
        "reads": [("timeline.py", 3), ("cli.py", 2), ("rules.py", 1)]}}
    line = _story_line(full)
    assert line.lstrip().startswith("↳ grep 'fromisoformat' ×2")
    assert "read 3 files ×6" in line
    assert "timeline.py ×3" in line and "cli.py ×2" in line
    assert "rules.py" not in line          # 次數 1 不列名
    layout = {"kind": "action", "story": {"searches": [("find -type d", "", 4)],
                                          "reads": []}}
    assert _story_line(layout).lstrip() == "↳ find -type d ×4"   # raw 空 → 只印 tool
    single = {"kind": "keyword", "story": {"searches": [("grep", "needle", 1)],
                                           "reads": [("a.py", 1)]}}
    line = _story_line(single)
    assert "×1" not in line                # ×N 僅 N≥2
    assert "read 1 file" in line and "files" not in line
    assert _story_line({"kind": "keyword", "story": {}}) is None


def test_session_block_thesis_story_and_legend(tmp_path, fake_repo):
    cwd = str(fake_repo)
    lines = [
        assistant(0, cwd, [tool_use("Grep", {"pattern": "needle_x"}, "t1")]),
        tool_result(1, cwd, "t1", "src/a.py:1: needle_x" + "x" * 380),
        assistant(2, cwd, [tool_use("Read", {"file_path": f"{fake_repo}/src/a.py"}, "t2")]),
        tool_result(3, cwd, "t2", "content"),
        assistant(4, cwd, [tool_use("Read", {"file_path": f"{fake_repo}/src/a.py"}, "t3")]),
        tool_result(5, cwd, "t3", "content"),  # 重讀 ×2 → basename 進實例句(規則 5)
        assistant(6, cwd, [tool_use("Grep", {"pattern": "needle_y"}, "t4")]),
        tool_result(7, cwd, "t4", "hit"),
    ]
    p = tmp_path / "s.jsonl"
    p.write_text(make_transcript(fake_repo, lines), encoding="utf-8")
    parsed = parse_file(p)
    comps = [c for c in scan_components(fake_repo) if c.provenance == "local"]
    att = attribute(parsed, comps, fake_repo)
    ext = extract_facts(parsed)
    agg = aggregate_session(parsed, att, comps, Config(), facts=ext.facts,
                            facts_tool_output=ext.tool_output_tokens_est)
    body = "\n".join(session_selfderive_lines(agg, full=True, style=Style()))
    assert "questions the harness didn't answer" in body      # thesis 進 session 區塊
    assert "% of tool output" in body                          # 分母有 → 百分比有
    assert "↳ grep 'needle_x'" in body                         # top-5 實例句
    assert "a.py" in body                                      # chain-read 檔名入句
    assert "~ = includes heuristic attribution" in body        # chain read 是 heuristic


def test_select_row_by_number_and_key():
    rows = [{"key": "repo layout", "label": "repo layout"},
            {"key": "retrypolicy", "label": "retry_policy"}]
    assert select_row(rows, "2")["key"] == "retrypolicy"
    assert select_row(rows, "repo layout")["key"] == "repo layout"
    assert select_row(rows, "retry_policy")["key"] == "retrypolicy"  # label matches too
    assert select_row(rows, "9") is None
    assert select_row(rows, "nope") is None
