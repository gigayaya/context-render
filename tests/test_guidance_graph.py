"""Reachability closure: reference edges + mechanic edges.

Toggle defaults are locked constants; only the constants ever change, never
the interface.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from context_render.guidance.graph import build_reach
from context_render.guidance.refs import FileIndex


@pytest.fixture
def mini_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "mini"
    (repo / "pkg" / "sub").mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "CLAUDE.md").write_text(
        "See `pkg/` and [guide](docs/guide.md).\n```bash\ncat pkg/fenced_only.py\n```\n",
        encoding="utf-8")
    (repo / "pkg" / "CLAUDE.md").write_text("`core.py` is the core; `sub/` below.",
                                            encoding="utf-8")
    (repo / "pkg" / "sub" / "CLAUDE.md").write_text("`leaf.py` lives here.", encoding="utf-8")
    (repo / "docs" / "guide.md").write_text("details in `pkg/deep.py`", encoding="utf-8")
    for f in ("pkg/core.py", "pkg/helper.py", "pkg/fenced_only.py", "pkg/deep.py",
              "pkg/sub/leaf.py", "orphan.py"):
        (repo / f).write_text("x", encoding="utf-8")
    return repo


@pytest.fixture
def index(mini_repo: Path) -> FileIndex:
    return FileIndex(mini_repo)


def _reach(mini_repo, index, **kw):
    return build_reach(mini_repo, index, starts=["CLAUDE.md"], **kw)


def test_reference_mechanic_chain_with_provenance(mini_repo, index):
    r = _reach(mini_repo, index)
    assert "pkg/core.py" in r.reachable
    assert r.hops["pkg/core.py"] == 2  # root(0) → pkg(1) → [mechanic 0-cost] pkg/CLAUDE.md → core(2)
    assert r.via["pkg/core.py"].carrier == "pkg/CLAUDE.md"
    assert r.via["pkg/core.py"].kind == "reference"
    assert r.via["pkg/CLAUDE.md"].kind == "mechanic"
    assert r.hops["pkg/CLAUDE.md"] == 1  # mechanic edges cost 0 hops


def test_nested_carrier_chain(mini_repo, index):
    r = _reach(mini_repo, index)
    assert "pkg/sub/leaf.py" in r.reachable
    assert r.hops["pkg/sub/leaf.py"] == 3
    assert r.via["pkg/sub/leaf.py"].carrier == "pkg/sub/CLAUDE.md"


def test_dir_children_toggle(mini_repo, index):
    on = _reach(mini_repo, index, dir_children=True)
    assert "pkg/helper.py" in on.reachable
    assert on.via["pkg/helper.py"].kind == "ls"
    off = _reach(mini_repo, index, dir_children=False)
    assert "pkg/helper.py" not in off.reachable


def test_all_md_toggle(mini_repo, index):
    on = _reach(mini_repo, index, all_md=True, dir_children=False)
    assert "pkg/deep.py" in on.reachable
    off = _reach(mini_repo, index, all_md=False, dir_children=False)
    assert "pkg/deep.py" not in off.reachable


def test_fenced_toggle(mini_repo, index):
    off = _reach(mini_repo, index, fenced=False, dir_children=False)
    assert "pkg/fenced_only.py" not in off.reachable
    on = _reach(mini_repo, index, fenced=True, dir_children=False)
    assert "pkg/fenced_only.py" in on.reachable


def test_orphan_never_reachable(mini_repo, index):
    r = _reach(mini_repo, index, fenced=True, all_md=True, dir_children=True)
    assert "orphan.py" not in r.reachable  # repo root is not an ls-able node


def test_missing_root_claude_md_is_signal_not_error(tmp_path):
    repo = tmp_path / "bare"
    repo.mkdir()
    (repo / "lonely.py").write_text("x", encoding="utf-8")
    r = build_reach(repo, FileIndex(repo), starts=["CLAUDE.md"])
    assert r.reachable == set()  # near-zero output is a signal, not an error


def test_external_carrier(mini_repo, index):
    r = build_reach(mini_repo, index, starts=[],
                    external_carriers={"<global>": "always read `pkg/core.py`"})
    assert "pkg/core.py" in r.reachable
    assert r.hops["pkg/core.py"] == 1
    assert r.via["pkg/core.py"].carrier == "<global>"


def test_locked_defaults():
    # Locked defaults (validated 2026-07-19 across 5 repos): changing one needs fresh
    # corpus evidence — this test is the tripwire.
    from context_render.guidance.graph import ALL_MD_DEFAULT, DIR_CHILDREN_DEFAULT, FENCED_DEFAULT
    assert FENCED_DEFAULT is True
    assert ALL_MD_DEFAULT is True
    assert DIR_CHILDREN_DEFAULT is True


def test_default_build_includes_fenced_routing(mini_repo, index):
    # tree-diagram routing inside fenced blocks counts by default
    r = _reach(mini_repo, index)
    assert "pkg/fenced_only.py" in r.reachable


def test_stale_collected_per_carrier(mini_repo, index):
    (mini_repo / "pkg" / "CLAUDE.md").write_text("`core.py`; legacy `gone.py`.",
                                                 encoding="utf-8")
    r = _reach(mini_repo, index)
    assert [s.raw for s in r.stale["pkg/CLAUDE.md"]] == ["gone.py"]


def test_ls_edge_exposes_direct_children_only(tmp_path):
    """a referenced dir is one `ls` away from its direct child files — never from files
    in its subdirectories, nor from files outside it."""
    from context_render.guidance.graph import Provenance

    repo = tmp_path / "lsrepo"
    (repo / "pkg" / "inner").mkdir(parents=True)
    (repo / "CLAUDE.md").write_text("start at `pkg/`", encoding="utf-8")
    for f in ("pkg/a.py", "pkg/b.py", "pkg/inner/c.py", "top.py"):
        (repo / f).write_text("x", encoding="utf-8")
    r = build_reach(repo, FileIndex(repo), starts=["CLAUDE.md"])
    assert r.hops["pkg"] == 1
    assert {f for f in r.reachable if f.endswith(".py")} == {"pkg/a.py", "pkg/b.py"}
    assert r.hops["pkg/a.py"] == r.hops["pkg/b.py"] == 2
    assert r.via["pkg/a.py"] == Provenance("pkg", "pkg/a.py", "ls")
