"""mapdev/classify.py — per-line guidance classification.

The classifier only combines two legal signals: markdown syntax (fences, headings,
table frames) and reference resolution against the real file tree (guidance/refs.py).
Paper-convention judgments (prose share thresholds, label rules) live in audit, not here.
"""

from pathlib import Path

import pytest

from context_render.guidance.refs import FileIndex
from context_render.mapdev.classify import classify_lines


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "parser").mkdir()
    (tmp_path / "parser" / "loader.py").write_text("x = 1\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("# guide\n")
    (tmp_path / "CLAUDE.md").write_text("placeholder\n")
    return tmp_path


def classify(text: str, repo: Path, carrier: str = "CLAUDE.md"):
    return classify_lines(text, carrier, FileIndex(repo))


def kinds(text: str, repo: Path, carrier: str = "CLAUDE.md") -> list[str]:
    return [li.kind for li in classify(text, repo, carrier)]


def test_routing_line_resolves_reference(repo):
    lines = classify("- `parser/loader.py` — JSONL loading\n", repo)
    assert lines[0].kind == "routing"
    assert lines[0].refs == ("parser/loader.py",)


def test_prose_line_has_no_reference(repo):
    assert kinds("This module follows a layered architecture.\n", repo) == ["prose"]


def test_unresolvable_path_mention_is_prose(repo):
    # dead-route detection rides the stale channel in audit; the classifier
    # only asks "does this line resolve", so a missing path stays prose
    assert kinds("see `missing/gone.py` for details\n", repo) == ["prose"]


def test_blank_line(repo):
    assert kinds("\n", repo) == ["blank"]


def test_fenced_block_is_code_including_delimiters(repo):
    text = "```bash\npython -m pytest\n`parser/loader.py`\n```\n"
    assert kinds(text, repo) == ["code", "code", "code", "code"]


def test_heading_without_reference(repo):
    assert kinds("# Overview\n", repo) == ["heading"]


def test_heading_with_reference_is_routing(repo):
    lines = classify("## `docs/`\n", repo)
    assert lines[0].kind == "routing"
    assert lines[0].refs == ("docs",)


def test_table_frame_is_structural(repo):
    assert kinds("|---|---|\n", repo) == ["structural"]


def test_table_row_resolves_relative_to_carrier(repo):
    # basename resolves layer-1 against the carrier's own directory
    lines = classify("| `loader.py` | JSONL loading |\n", repo,
                     carrier="parser/CLAUDE.md")
    assert lines[0].kind == "routing"
    assert lines[0].refs == ("parser/loader.py",)


def test_line_numbers_are_one_based_and_complete(repo):
    text = "# Title\n\n- `parser/loader.py` — loader\nprose here\n"
    lines = classify(text, repo)
    assert [li.number for li in lines] == [1, 2, 3, 4]
    assert [li.kind for li in lines] == ["heading", "blank", "routing", "prose"]
