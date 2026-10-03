"""mapdev/initgen.py — routing-map skeleton generation.

The skeleton is deterministic structure only: paths + TODO labels. Semantic labels are
the user's agent's job (fill instructions); the generator must never emit prose, and its
output must itself pass the audit clean (self-consistency).
"""

from pathlib import Path

import pytest

from context_render.guidance.refs import FileIndex
from context_render.mapdev.audit import aggregate_map
from context_render.mapdev.initgen import FLAT_THRESHOLD, build_skeleton, fill_instructions


def make_repo(tmp_path: Path, rels: list[str]) -> FileIndex:
    for rel in rels:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x\n")
    return FileIndex(tmp_path)


def test_flat_skeleton_lists_every_file_once(tmp_path):
    index = make_repo(tmp_path, ["top.py", "parser/a.py", "docs/guide.md"])
    text = build_skeleton(index, shape="flat")
    assert "- `top.py` — TODO: one-line label" in text
    assert "- `parser/a.py` — TODO: one-line label" in text
    assert "- `docs/guide.md` — TODO: one-line label" in text
    assert "## " not in text


def test_skeleton_excludes_claude_md_files(tmp_path):
    index = make_repo(tmp_path, ["a.py", "CLAUDE.md", "parser/CLAUDE.md", "parser/b.py"])
    text = build_skeleton(index, shape="flat")
    assert "CLAUDE.md" not in text


def test_tree_skeleton_groups_by_top_level_directory(tmp_path):
    index = make_repo(tmp_path, ["top.py", "parser/a.py", "parser/b.py",
                                 "docs/guide.md"])
    text = build_skeleton(index, shape="tree")
    lines = text.splitlines()
    assert "- `top.py` — TODO: one-line label" in lines
    assert "## `docs/` — TODO: one-line label" in lines
    assert "## `parser/` — TODO: one-line label" in lines
    assert lines.index("## `docs/` — TODO: one-line label") < lines.index(
        "## `parser/` — TODO: one-line label")
    assert "- `parser/a.py` — TODO: one-line label" in lines


def test_auto_shape_flips_at_threshold(tmp_path):
    small = make_repo(tmp_path / "small", ["a.py", "b/c.py"])
    assert "## " not in build_skeleton(small, shape="auto")
    big = make_repo(tmp_path / "big",
                    [f"pkg/f{i:04d}.py" for i in range(FLAT_THRESHOLD + 1)])
    assert "## `pkg/` — TODO: one-line label" in build_skeleton(big, shape="auto")


def test_skeleton_is_deterministic(tmp_path):
    index = make_repo(tmp_path, ["b.py", "a.py", "z/x.py"])
    assert build_skeleton(index, "tree") == build_skeleton(index, "tree")


@pytest.mark.parametrize("shape", ["flat", "tree"])
def test_skeleton_passes_its_own_audit(tmp_path, shape):
    index = make_repo(tmp_path, ["top.py", "parser/a.py", "docs/guide.md"])
    (tmp_path / "CLAUDE.md").write_text(build_skeleton(index, shape=shape))
    agg = aggregate_map(FileIndex(tmp_path))  # re-index: the map itself now exists
    root = next(c for c in agg["carriers"] if c["path"] == "CLAUDE.md")
    assert root["prose_share"] == 0.0
    assert agg["dead_routes"] == []
    assert root["bare_paths"] == 0
    assert root["label_echoes"] == 0
    assert agg["depth"]["over3"] == []


def test_fill_instructions_state_the_contract(tmp_path):
    text = fill_instructions()
    assert "TODO" in text
    assert "single authority" in text
    assert "architectural prose" in text
    assert "ctxr map" in text
    assert "ctxr map audit" not in text   # the subcommand is gone; `ctxr map` is the report
