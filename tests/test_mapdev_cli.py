"""`ctxr map` / `ctxr map init` — CLI flow, exit codes, no-overwrite discipline."""

from pathlib import Path

from typer.testing import CliRunner

from context_render.cli import app

runner = CliRunner()


def make_repo(tmp_path: Path, monkeypatch, files: dict[str, str]) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return tmp_path


def test_map_runs_statically_without_init(tmp_path, monkeypatch):
    make_repo(tmp_path, monkeypatch, {"CLAUDE.md": "- `a.py` — alpha\n", "a.py": ""})
    monkeypatch.setenv("HOME", str(tmp_path))  # a real ~/.claude/CLAUDE.md must not leak in
    result = runner.invoke(app, ["map"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("Map — ")
    assert "no observed searches" in result.output


def test_map_md_writes_report_file(tmp_path, monkeypatch):
    make_repo(tmp_path, monkeypatch, {"CLAUDE.md": "- `a.py` — alpha\n", "a.py": ""})
    result = runner.invoke(app, ["map", "--md"])
    assert result.exit_code == 0, result.output
    reports = list((tmp_path / ".context-render" / "reports").glob("map-*.md"))
    assert len(reports) == 1
    assert not list((tmp_path / ".context-render").glob("db.sqlite"))  # never creates a db


def test_map_without_root_claude_md_is_a_signal_not_an_error(tmp_path, monkeypatch):
    make_repo(tmp_path, monkeypatch, {"x.py": "def a(): ...\n"})
    result = runner.invoke(app, ["map"])
    assert result.exit_code == 0, result.output
    assert "no root CLAUDE.md" in result.output


def test_map_since_bad_spec_is_precondition_error(tmp_path, monkeypatch):
    make_repo(tmp_path, monkeypatch, {"CLAUDE.md": "- `a.py` — alpha\n", "a.py": ""})
    result = runner.invoke(app, ["map", "--since", "yesterday-ish"])
    assert result.exit_code == 3


def test_map_since_label_flows_into_report(tmp_path, monkeypatch):
    make_repo(tmp_path, monkeypatch, {"CLAUDE.md": "- `a.py` — alpha\n", "a.py": "",
                                      "b.py": "def b(): ...\n"})
    result = runner.invoke(app, ["map", "--since", "7d"])
    assert result.exit_code == 0, result.output
    assert "last 7d" in result.output


def test_old_commands_are_gone(tmp_path, monkeypatch):
    make_repo(tmp_path, monkeypatch, {"CLAUDE.md": "- `a.py` — alpha\n", "a.py": ""})
    assert runner.invoke(app, ["coverage"]).exit_code == 2
    assert runner.invoke(app, ["map", "audit"]).exit_code == 2


def test_map_init_hint_names_the_merged_command(tmp_path, monkeypatch):
    make_repo(tmp_path, monkeypatch, {"a.py": ""})
    result = runner.invoke(app, ["map", "init"])
    assert result.exit_code == 0, result.output
    assert "run `ctxr map`" in result.output
    assert "map audit" not in result.output


def test_map_init_creates_claude_md_when_absent(tmp_path, monkeypatch):
    make_repo(tmp_path, monkeypatch, {"a.py": ""})
    result = runner.invoke(app, ["map", "init"])
    assert result.exit_code == 0, result.output
    assert "Repository routing map" in (tmp_path / "CLAUDE.md").read_text()
    instr = tmp_path / ".context-render" / "map-fill-instructions.md"
    assert "single authority" in instr.read_text()
    assert "CLAUDE.md" in result.output


def test_map_init_writes_proposal_when_claude_md_exists(tmp_path, monkeypatch):
    make_repo(tmp_path, monkeypatch,
              {"CLAUDE.md": "existing content\n", "a.py": ""})
    result = runner.invoke(app, ["map", "init"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "CLAUDE.md").read_text() == "existing content\n"
    proposal = tmp_path / ".context-render" / "map-proposal.md"
    assert "Repository routing map" in proposal.read_text()


def test_map_init_never_overwrites_a_proposal(tmp_path, monkeypatch):
    make_repo(tmp_path, monkeypatch, {"CLAUDE.md": "keep\n", "a.py": ""})
    assert runner.invoke(app, ["map", "init"]).exit_code == 0
    (tmp_path / ".context-render" / "map-proposal.md").write_text("agent filled\n")
    result = runner.invoke(app, ["map", "init"])
    assert result.exit_code == 3
    assert (tmp_path / ".context-render" / "map-proposal.md").read_text() == "agent filled\n"


def test_map_init_shape_tree(tmp_path, monkeypatch):
    make_repo(tmp_path, monkeypatch, {"pkg/a.py": "", "pkg/b.py": ""})
    result = runner.invoke(app, ["map", "init", "--shape", "tree"])
    assert result.exit_code == 0, result.output
    assert "## `pkg/` — TODO: one-line label" in (tmp_path / "CLAUDE.md").read_text()


def test_map_init_rejects_unknown_shape(tmp_path, monkeypatch):
    make_repo(tmp_path, monkeypatch, {"a.py": ""})
    assert runner.invoke(app, ["map", "init", "--shape", "spiral"]).exit_code == 3


def test_map_init_output_flag(tmp_path, monkeypatch):
    make_repo(tmp_path, monkeypatch, {"CLAUDE.md": "keep\n", "a.py": ""})
    result = runner.invoke(app, ["map", "init", "--output", "maps/newmap.md"])
    assert result.exit_code == 0, result.output
    assert "Repository routing map" in (tmp_path / "maps" / "newmap.md").read_text()
    assert (tmp_path / "CLAUDE.md").read_text() == "keep\n"
    assert (tmp_path / ".context-render" / "map-fill-instructions.md").exists()


def test_map_init_output_refuses_existing_target(tmp_path, monkeypatch):
    make_repo(tmp_path, monkeypatch,
              {"CLAUDE.md": "keep\n", "maps/newmap.md": "mine\n", "a.py": ""})
    result = runner.invoke(app, ["map", "init", "--output", "maps/newmap.md"])
    assert result.exit_code == 3
    assert (tmp_path / "maps" / "newmap.md").read_text() == "mine\n"
