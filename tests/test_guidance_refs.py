"""Format-neutral path-reference extraction.

Dry-run findings (2026-07-19) are frozen here as regression tests:
bare-basename references, and slash-idiom stale pollution.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from context_render.guidance.refs import FileIndex, Ref, StaleRef, extract_refs


@pytest.fixture
def refs_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "proj"
    (repo / "context_render" / "attributor").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "a").mkdir()
    (repo / "b").mkdir()
    (repo / ".context-render").mkdir()
    for f in (
        "CLAUDE.md",
        "context_render/CLAUDE.md",
        "context_render/pipeline.py",
        "context_render/cli.py",
        "context_render/attributor/rules.py",
        "tests/CLAUDE.md",
        "tests/test_cli.py",
        "docs/guide.md",
        "a/utils.py",
        "b/utils.py",
        ".context-render/manifest.yaml",
    ):
        (repo / f).write_text("x", encoding="utf-8")
    return repo


@pytest.fixture
def index(refs_repo: Path) -> FileIndex:
    return FileIndex(refs_repo)


def _targets(refs: list[Ref]) -> list[str]:
    return [r.target for r in refs]


# ---- 層 1/2:相對解析(exact)----

def test_inline_relative_to_carrier_is_exact(index):
    refs, stale = extract_refs("see `pipeline.py`.", "context_render/CLAUDE.md", index)
    assert refs == [Ref("context_render/pipeline.py", False, "inline", "pipeline.py", "exact")]
    assert stale == []


def test_repo_root_fallback_is_exact(index):
    refs, _ = extract_refs("run `context_render/cli.py`", "tests/CLAUDE.md", index)
    assert _targets(refs) == ["context_render/cli.py"]
    assert refs[0].confidence == "exact"


def test_markdown_link_target(index):
    refs, _ = extract_refs("read [the guide](docs/guide.md) first", "CLAUDE.md", index)
    assert refs == [Ref("docs/guide.md", False, "link", "docs/guide.md", "exact")]


def test_prose_path_token(index):
    refs, _ = extract_refs("conventions live in docs/guide.md, read them", "CLAUDE.md", index)
    assert _targets(refs) == ["docs/guide.md"]
    assert refs[0].context == "prose"


# ---- 層 3:unique-basename(heuristic)(迴歸:發現一)----

def test_unique_basename_is_heuristic(index):
    refs, stale = extract_refs("entry point is `cli.py`", "CLAUDE.md", index)
    assert refs == [Ref("context_render/cli.py", False, "inline", "cli.py", "heuristic")]
    assert stale == []


def test_ambiguous_basename_abstains(index):
    # 兩個 utils.py → 不建邊、不入 stale(棄權是刻意 false negative)
    refs, stale = extract_refs("helpers in `utils.py`", "CLAUDE.md", index)
    assert refs == [] and stale == []


def test_nonexistent_basename_no_edge(index):
    # 迴歸(發現一反例):`site.py` 指標準庫,repo 無此檔 → 不建邊;含副檔名 → stale
    refs, stale = extract_refs("macOS `site.py` issue", "CLAUDE.md", index)
    assert refs == []
    assert stale == [StaleRef("site.py", "inline")]


# ---- stale 從嚴(迴歸:發現四)----

def test_slash_idiom_is_not_stale(index):
    text = "three-state R/L/I model, confidence exact/heuristic, `command/option` parsing"
    refs, stale = extract_refs(text, "CLAUDE.md", index)
    assert refs == [] and stale == []


def test_missing_file_with_ext_is_stale(index):
    refs, stale = extract_refs("see `old_rules.py`", "context_render/attributor/CLAUDE.md", index)
    assert refs == []
    assert stale == [StaleRef("old_rules.py", "inline")]


def test_missing_path_with_existing_first_segment_is_stale(index):
    refs, stale = extract_refs("see `attributor/gone.py`", "context_render/CLAUDE.md", index)
    assert refs == []
    assert stale == [StaleRef("attributor/gone.py", "inline")]


def test_runtime_artifact_not_edge_not_stale(index):
    # runtime 產物依真實檔案系統解析——存在但被 SKIP 的路徑既非邊也非 stale
    refs, stale = extract_refs("state in `.context-render/manifest.yaml`", "CLAUDE.md", index)
    assert refs == [] and stale == []


# ---- fenced context 標記 ----

def test_fenced_refs_tagged_fenced(index):
    text = "prose `pipeline.py` here\n```bash\ncat context_render/cli.py\n```\n"
    refs, _ = extract_refs(text, "context_render/CLAUDE.md", index)
    by_ctx = {r.context: r.target for r in refs}
    assert by_ctx["inline"] == "context_render/pipeline.py"
    assert by_ctx["fenced"] == "context_render/cli.py"


# ---- dir/glob/排除 ----

def test_dir_ref_trailing_slash(index):
    refs, _ = extract_refs("subpackage `attributor/` has its own CLAUDE.md",
                           "context_render/CLAUDE.md", index)
    assert refs[0] == Ref("context_render/attributor", True, "inline", "attributor/", "exact")


def test_glob_expansion(index):
    refs, _ = extract_refs("all of `tests/test_*.py`", "CLAUDE.md", index)
    assert _targets(refs) == ["tests/test_cli.py"]


def test_url_and_env_not_candidates(index):
    text = "docs at https://x.example/a.py, config `$HOME/x.py`, home `~/y.py`"
    refs, stale = extract_refs(text, "CLAUDE.md", index)
    assert refs == [] and stale == []


def test_duplicate_refs_deduped(index):
    refs, _ = extract_refs("`cli.py` then `cli.py` again", "context_render/CLAUDE.md", index)
    assert _targets(refs) == ["context_render/cli.py"]


# ---- stale hygiene 迴歸(2026-07-19 跨專案 dry-run)----

def test_template_placeholder_not_stale(index):
    # `lib/<domain>/constants.py` 型佔位符:不可能是 repo rot
    refs, stale = extract_refs(
        "put them in `lib/<domain>/constants.py`, tests in `tests/<domain>`",
        "CLAUDE.md", index)
    assert refs == [] and stale == []


def test_leading_slash_never_candidate(index):
    # 絕對路徑(/usr/src/insect)、slash-command(/docs-drift)、XML tag(/svg)
    refs, stale = extract_refs(
        "run `/docs-drift`; docker uses `/usr/src/insect`; close with `/svg`",
        "CLAUDE.md", index)
    assert refs == [] and stale == []


# ---- FileIndex ----

def test_index_skips_conventional_dirs(tmp_path):
    repo = tmp_path / "p"
    (repo / ".venv" / "lib").mkdir(parents=True)
    (repo / ".venv" / "lib" / "x.py").write_text("x", encoding="utf-8")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "y.py").write_text("x", encoding="utf-8")
    (repo / "real.py").write_text("x", encoding="utf-8")
    (repo / ".DS_Store").write_text("x", encoding="utf-8")   # dotfiles skipped
    (repo / ".gitignore").write_text("x", encoding="utf-8")
    idx = FileIndex(repo)
    assert idx.files == {"real.py"}
