"""Coverage aggregation + analyze-facts join (guidance-reachability spec §5).

Join discipline: attribution of observed searches to files rides on chain_read facts
(heuristic by construction) — sorting unreachable rows by observed cost is what keeps the
report a gauge (real pain first) instead of a lint tool ("document everything")."""

from __future__ import annotations

from pathlib import Path

import pytest

from context_render.guidance.graph import build_reach
from context_render.guidance.refs import FileIndex
from context_render.report.coverage import aggregate_coverage, join_facts


@pytest.fixture
def cov_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "cov"
    (repo / "pkg").mkdir(parents=True)
    (repo / "CLAUDE.md").write_text("see `pkg/`", encoding="utf-8")
    (repo / "pkg" / "CLAUDE.md").write_text("`core.py` only", encoding="utf-8")
    (repo / "pkg" / "core.py").write_text("def a(): ...\ndef b(): ...\n", encoding="utf-8")
    (repo / "pkg" / "context_map.py").write_text(
        "def m1(): ...\ndef m2(): ...\ndef m3(): ...\n", encoding="utf-8")
    (repo / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    return repo


def _agg(repo, store=None, fact_rows=None):
    index = FileIndex(repo)
    reach = build_reach(repo, index, starts=["CLAUDE.md"], dir_children=False)
    return aggregate_coverage(reach, index, store=store, since_iso=None,
                              _fact_rows=fact_rows)


def _search(key, tokens, sid="s1", idx=0):
    return {"session_id": sid, "idx": idx, "kind": "search", "key": key, "raw": key,
            "tool": "grep", "tokens_est": tokens, "occupancy_est": None, "sidechain": 0,
            "confidence": "exact"}


def _chain(key, raw, sid="s1", idx=1):
    # raw is repo-relative (facts extractor v3) — the joinable form
    return {"session_id": sid, "idx": idx, "kind": "chain_read", "key": key, "raw": raw,
            "tool": "Read", "tokens_est": 0, "occupancy_est": None, "sidechain": 0,
            "confidence": "heuristic"}


def test_counts_and_hops(cov_repo):
    agg = _agg(cov_repo)
    assert agg["files"] == {"total": 5, "reachable": 3}   # reachable: CLAUDE.md ×2 + core.py
    assert agg["py"] == {"total": 3, "reachable": 1}
    assert agg["symbols"] == {"total": 5, "reachable": 2}  # broken.py 不計入 total
    assert agg["parse_failed"] == 1
    assert agg["hop_dist"] == {2: 1}
    assert agg["joined"] is False


def test_unreachable_sorted_by_observed_cost(cov_repo):
    rows = [
        _search("contextmap", 9000),
        _chain("contextmap", "pkg/context_map.py"),
    ]
    agg = _agg(cov_repo, fact_rows=rows)
    assert agg["joined"] is True
    top = agg["unreachable"][0]
    assert top["path"] == "pkg/context_map.py"
    assert top["defs"] == 3 and top["grep_count"] == 1 and top["tokens_est"] == 9000
    # 無觀測成本者殿後
    assert [r["path"] for r in agg["unreachable"]][-1] == "broken.py"


def test_grepped_but_reachable(cov_repo):
    rows = [
        _search("corea", 500),
        _chain("corea", "pkg/core.py"),
    ]
    agg = _agg(cov_repo, fact_rows=rows)
    assert agg["grepped_but_reachable"] == [
        {"path": "pkg/core.py", "hop": 2, "grep_count": 1, "tokens_est": 500}]


def test_unmappable_chain_read_ignored(cov_repo):
    # absolute raws (pre-v3 extractions / outside-repo reads) never join — prefer a miss
    rows = [_search("x", 100), _chain("x", "/somewhere/else/context_map.py"),
            _search("y", 100, idx=2), _chain("y", str(cov_repo / "pkg" / "core.py"), idx=3)]
    agg = _agg(cov_repo, fact_rows=rows)
    assert all(r["grep_count"] == 0 for r in agg["unreachable"])
    assert agg["grepped_but_reachable"] == []


def test_no_store_degrades_gracefully(cov_repo):
    agg = _agg(cov_repo, store=None)
    assert agg["joined"] is False
    assert all(r["grep_count"] == 0 and r["tokens_est"] == 0 for r in agg["unreachable"])


def test_stale_flattened(cov_repo):
    (cov_repo / "pkg" / "CLAUDE.md").write_text("`core.py`; old `gone.py`", encoding="utf-8")
    agg = _agg(cov_repo)
    assert agg["stale"] == [{"carrier": "pkg/CLAUDE.md", "raw": "gone.py"}]


# ---- 渲染 + CLI(Task 6)----

def test_coverage_lines_term(cov_repo):
    from context_render.config import Config
    from context_render.report.render_term import coverage_lines
    rows = [_search("contextmap", 9000),
            _chain("contextmap", "pkg/context_map.py")]
    agg = _agg(cov_repo, fact_rows=rows)
    out = "\n".join(coverage_lines(agg, Config(), full=False))
    assert "Guidance reachability — 3/5 files from root CLAUDE.md" in out
    assert "py: 1/3" in out and "symbols: 2/5" in out
    assert "necessary, not sufficient" in out
    assert "pkg/context_map.py" in out and "~9k tokens" in out
    # 未觀測者殿後且無成本欄贅字
    assert out.index("pkg/context_map.py") < out.index("broken.py")


def test_coverage_lines_degraded_note(cov_repo):
    from context_render.config import Config
    from context_render.report.render_term import coverage_lines
    out = "\n".join(coverage_lines(_agg(cov_repo), Config(), full=False))
    assert "no observed searches" in out


def test_cli_coverage(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from context_render.cli import app
    repo = tmp_path / "r"
    (repo / "pkg").mkdir(parents=True)
    (repo / "CLAUDE.md").write_text("see `pkg/`", encoding="utf-8")
    (repo / "pkg" / "CLAUDE.md").write_text("`core.py`", encoding="utf-8")
    (repo / "pkg" / "core.py").write_text("def a(): ...\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(tmp_path))  # 隔離 global CLAUDE.md
    runner = CliRunner()
    r = runner.invoke(app, ["coverage"])
    assert r.exit_code == 0, r.output
    assert "Guidance reachability" in r.output
    r_md = runner.invoke(app, ["coverage", "--md"])
    assert r_md.exit_code == 0
    assert list((repo / ".context-render" / "reports").glob("coverage-*.md"))


def test_cli_coverage_without_root_claude_md(tmp_path, monkeypatch):
    # 起點缺席是訊號不是錯誤:exit 0 + 註明
    from typer.testing import CliRunner
    from context_render.cli import app
    repo = tmp_path / "bare"
    repo.mkdir()
    (repo / "x.py").write_text("def a(): ...\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(tmp_path))
    r = CliRunner().invoke(app, ["coverage"])
    assert r.exit_code == 0, r.output
    assert "no root CLAUDE.md" in r.output


def test_join_facts_multiple_sessions():
    reachable = {"pkg/a.py"}
    rows = [
        _search("k1", 100, sid="s1"), _chain("k1", "pkg/a.py", sid="s1"),
        _search("k1", 200, sid="s2", idx=0), _chain("k1", "pkg/a.py", sid="s2"),
    ]
    per_file = join_facts(rows, reachable | set())
    assert per_file["pkg/a.py"] == {"grep_count": 2, "tokens_est": 300}


def test_join_from_real_store_ingest(fake_repo, fake_projects):
    """Regression (the v2 bug): sessions.path stores the TRANSCRIPT .jsonl path, not the
    repo root — the join must not lean on it. chain_read raws are repo-relative at ingest
    (extractor v3), so coverage over a really-synced store attributes observed cost."""
    from context_render.config import Config
    from context_render.pipeline import scan_repo
    from context_render.store import Store
    from tests.test_analyze import _add_session, _prep, search_lines

    (fake_repo / "src" / "retry.py").write_text("def retry(): ...\n", encoding="utf-8")
    _prep(fake_repo)
    _add_session(fake_projects, fake_repo, "33333333-aaaa-bbbb-cccc-000000000003",
                 search_lines(fake_repo))
    scan_repo(fake_repo, Config(), projects_dir=fake_projects)

    index = FileIndex(fake_repo)
    reach = build_reach(fake_repo, index, starts=["CLAUDE.md"], dir_children=False)
    store = Store(fake_repo / ".context-render" / "db.sqlite")
    try:
        agg = aggregate_coverage(reach, index, store=store, since_iso=None)
    finally:
        store.close()
    row = next(r for r in agg["unreachable"] + agg["grepped_but_reachable"]
               if r["path"] == "src/retry.py")
    assert row["grep_count"] >= 1 and row["tokens_est"] > 0
