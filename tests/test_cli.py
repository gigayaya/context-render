"""CLI tests: exit-code convergence (0/2/3), init flow, hookinstall idempotency."""

import json

import pytest
from typer.testing import CliRunner

from context_render import hookinstall
from context_render.cli import app
from context_render.errors import PreconditionError

runner = CliRunner()


def test_exit_2_on_usage_error():
    result = runner.invoke(app, ["sync", "--bogus-flag"])
    assert result.exit_code == 2  # CLI argument errors stay 2 (v3 decision #11)


def test_exit_3_without_manifest(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 3
    assert "init" in result.output or "manifest" in result.output


def test_exit_3_bad_since(fake_repo, monkeypatch):
    monkeypatch.chdir(fake_repo)
    runner.invoke(app, ["init", "--yes", "--no-hook"])
    result = runner.invoke(app, ["report", "--since", "notaspec"])
    assert result.exit_code == 3


def test_init_and_refresh(fake_repo, monkeypatch):
    monkeypatch.chdir(fake_repo)
    (fake_repo / ".git").mkdir()
    r1 = runner.invoke(app, ["init", "--yes", "--no-hook"])
    assert r1.exit_code == 0, r1.output
    assert (fake_repo / ".context-render" / "manifest.yaml").exists()
    # .gitignore appended
    gi = (fake_repo / ".gitignore").read_text(encoding="utf-8")
    assert ".context-render/db.sqlite" in gi
    # manifest exists and no --refresh → exit 3
    r2 = runner.invoke(app, ["init", "--yes", "--no-hook"])
    assert r2.exit_code == 3
    r3 = runner.invoke(app, ["init", "--refresh", "--yes", "--no-hook"])
    assert r3.exit_code == 0


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "context-render" in result.output


def test_hookinstall_idempotent(fake_repo):
    assert hookinstall.install(fake_repo) is True
    assert hookinstall.install(fake_repo) is False  # idempotent: no duplicate insertion
    data = json.loads((fake_repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
    groups = data["hooks"]["SessionEnd"]
    cmds = [h["command"] for g in groups for h in g["hooks"]]
    assert cmds.count("context-render sync --since 1d") == 1
    # existing hooks are not broken
    assert "PreToolUse" in data["hooks"]
    assert hookinstall.uninstall(fake_repo) is True
    assert hookinstall.is_installed(fake_repo) is False


def test_init_yes_takes_hook_prompt_default_no(fake_repo, monkeypatch):
    """--yes means "accept defaults" — the hook prompt defaults to No, so no unattended install."""
    monkeypatch.chdir(fake_repo)
    (fake_repo / ".git").mkdir()
    r = runner.invoke(app, ["init", "--yes"])
    assert r.exit_code == 0, r.output
    assert hookinstall.is_installed(fake_repo) is False
    # explicit --hook still installs unattended
    r2 = runner.invoke(app, ["init", "--refresh", "--yes", "--hook"])
    assert r2.exit_code == 0, r2.output
    assert hookinstall.is_installed(fake_repo) is True


def test_hookinstall_preserves_unparseable_settings(fake_repo):
    """corrupt settings.json must never be overwritten with just the hook."""
    sp = fake_repo / ".claude" / "settings.json"
    corrupt = '{"permissions": {"allow": ["Bash(npm:*)"]},}'  # trailing comma
    sp.write_text(corrupt, encoding="utf-8")
    with pytest.raises(PreconditionError):
        hookinstall.install(fake_repo)
    assert sp.read_text(encoding="utf-8") == corrupt  # untouched


def test_hookinstall_rejects_non_object_settings(fake_repo):
    sp = fake_repo / ".claude" / "settings.json"
    sp.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(PreconditionError):
        hookinstall.install(fake_repo)
    assert sp.read_text(encoding="utf-8") == "[1, 2]"


def test_hookinstall_tolerates_malformed_hook_groups(fake_repo):
    """settings.json with non-dict groups/hooks must not crash is_installed/uninstall."""
    sp = fake_repo / ".claude" / "settings.json"
    sp.write_text(
        json.dumps({"hooks": {"SessionEnd": ["oops", {"hooks": ["nothook"]}]}}),
        encoding="utf-8",
    )
    assert hookinstall.is_installed(fake_repo) is False
    assert hookinstall.uninstall(fake_repo) is False


def test_uninstall_no_match_leaves_file_untouched(fake_repo):
    """uninstall must not rewrite (reformat) settings.json when it removed nothing."""
    sp = fake_repo / ".claude" / "settings.json"
    original = json.dumps(
        {"hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": "other.sh"}]}]}}
    )
    sp.write_text(original, encoding="utf-8")
    assert hookinstall.uninstall(fake_repo) is False
    assert sp.read_text(encoding="utf-8") == original


def test_remove_hook_command(fake_repo, monkeypatch):
    monkeypatch.chdir(fake_repo)
    hookinstall.install(fake_repo)
    r = runner.invoke(app, ["remove-hook"])
    assert r.exit_code == 0, r.output
    assert hookinstall.is_installed(fake_repo) is False
    r2 = runner.invoke(app, ["remove-hook"])
    assert r2.exit_code == 0
    assert "nothing to remove" in r2.output.lower()


def test_sessions_and_last_session(fake_repo, fake_projects, monkeypatch):
    monkeypatch.chdir(fake_repo)
    monkeypatch.setenv("CONTEXT_RENDER_PROJECTS_DIR", str(fake_projects))
    runner.invoke(app, ["init", "--yes", "--no-hook"])
    assert runner.invoke(app, ["sync"]).exit_code == 0

    r = runner.invoke(app, ["sessions"])
    assert r.exit_code == 0
    assert "11111111" in r.output  # fixture session id prefix
    assert "turns" in r.output

    # sessions <id-prefix> targets one (prefix match; not affected by in-progress exclusion)
    r2 = runner.invoke(app, ["sessions", "11111111"])
    assert r2.exit_code == 0
    assert "timeline" in r2.output

    # not found → exit 3
    r3 = runner.invoke(app, ["sessions", "deadbeef"])
    assert r3.exit_code == 3


def test_full_flag_untruncates_terminal_report(fake_repo, fake_projects, monkeypatch):
    monkeypatch.chdir(fake_repo)
    monkeypatch.setenv("CONTEXT_RENDER_PROJECTS_DIR", str(fake_projects))
    runner.invoke(app, ["init", "--yes", "--no-hook"])
    assert runner.invoke(app, ["sync"]).exit_code == 0
    # Force timeline truncation so --full has something observable to lift
    cfg = fake_repo / ".context-render" / "config.yaml"
    cfg.write_text("timeline_term_max: 1\nin_progress_minutes: 0\n", encoding="utf-8")

    r = runner.invoke(app, ["sessions", "11111111"])
    assert r.exit_code == 0
    assert "truncated" in r.output
    r_full = runner.invoke(app, ["sessions", "11111111", "--full"])
    assert r_full.exit_code == 0
    assert "truncated" not in r_full.output

    assert runner.invoke(app, ["last", "--full"]).exit_code == 0


def test_sessions_without_sync(fake_repo, fake_projects, monkeypatch):
    monkeypatch.chdir(fake_repo)
    monkeypatch.setenv("CONTEXT_RENDER_PROJECTS_DIR", str(fake_projects))
    runner.invoke(app, ["init", "--yes", "--no-hook"])
    r = runner.invoke(app, ["sessions"])
    assert r.exit_code == 0
    assert "run context-render sync" in r.output


def test_clear(fake_repo, fake_projects, monkeypatch):
    monkeypatch.chdir(fake_repo)
    monkeypatch.setenv("CONTEXT_RENDER_PROJECTS_DIR", str(fake_projects))
    runner.invoke(app, ["init", "--yes", "--no-hook"])
    runner.invoke(app, ["sync"])
    reports_dir = fake_repo / ".context-render" / "reports"
    runner.invoke(app, ["sessions", "11111111", "--md"])
    assert (fake_repo / ".context-render" / "db.sqlite").exists()
    assert reports_dir.exists()

    # not confirmed → don't delete
    r0 = runner.invoke(app, ["clear"], input="n\n")
    assert r0.exit_code == 0
    assert (fake_repo / ".context-render" / "db.sqlite").exists()

    r = runner.invoke(app, ["clear", "--yes"])
    assert r.exit_code == 0
    assert not (fake_repo / ".context-render" / "db.sqlite").exists()
    assert not reports_dir.exists()
    # manifest and config are kept
    assert (fake_repo / ".context-render" / "manifest.yaml").exists()
    # run again: nothing to clear
    r2 = runner.invoke(app, ["clear", "--yes"])
    assert r2.exit_code == 0
    assert "Nothing to clear" in r2.output
    # after clearing, sync can rebuild
    assert runner.invoke(app, ["sync"]).exit_code == 0
    assert (fake_repo / ".context-render" / "db.sqlite").exists()


def test_help_command():
    r = runner.invoke(app, ["help"])
    assert r.exit_code == 0
    for cmd in ("init", "sync", "sessions", "last", "report", "deadweight", "clear",
                "remove-hook", "help"):
        assert cmd in r.output
    assert "Three states" in r.output
