"""Transcript-derived text must not carry terminal control sequences into reports or the DB:
a session being reviewed must not be able to rewrite the terminal it is reviewed in."""

from context_render.attributor import attribute
from context_render.config import Config
from context_render.inventory.scanner import scan_components
from context_render.parser import parse_file
from context_render.report.aggregate import aggregate_session
from context_render.report.render_md import render_md
from context_render.textutil import clean
from tests.conftest import _line, assistant, make_transcript, tool_result, tool_use, user_text


def test_clean_strips_ansi_and_controls():
    assert clean("a\x1b[31mred\x1b[0mb") == "aredb"  # whole CSI sequences removed
    assert clean("\x1b]0;evil\x07hello") == "hello"  # OSC (title set) removed with payload
    assert clean("bell\x07null\x00") == "bellnull"  # stray C0 controls dropped
    assert clean("中文 ok\ttab\nnl") == "中文 ok\ttab\nnl"  # text, tab, newline survive


def _att(tmp_path, fake_repo, lines):
    p = tmp_path / "s.jsonl"
    p.write_text(make_transcript(fake_repo, lines), encoding="utf-8")
    parsed = parse_file(p)
    comps = [c for c in scan_components(fake_repo) if c.provenance == "local"]
    return parsed, comps, attribute(parsed, comps, fake_repo)


def test_timeline_digest_and_paths_sanitized(tmp_path, fake_repo):
    cwd = str(fake_repo)
    lines = [
        user_text(0, cwd, "\x1b]0;evil\x07please fix the bug"),
        assistant(1, cwd, [tool_use("Bash", {"command": "echo \x1b[31mhi\x1b[0m"}, "t1")]),
        tool_result(2, cwd, "t1", "hi"),
        assistant(3, cwd, [tool_use("Read", {"file_path": f"{cwd}/docs/\x1b[31mx.md"}, "t2")]),
        tool_result(4, cwd, "t2", "content"),
    ]
    _, _, att = _att(tmp_path, fake_repo, lines)
    assert att.prompt_digest == "please fix the bug"
    joined = "".join(t.detail for t in att.timeline)
    assert "\x1b" not in joined and "\x07" not in joined
    assert all("\x1b" not in fr.path for fr in att.file_reads)


def test_cc_version_sanitized(tmp_path, fake_repo):
    lines = [user_text(0, str(fake_repo), "hi", version="2.1.207\x1b[2J")]
    parsed, _, _ = _att(tmp_path, fake_repo, lines)
    assert parsed.cc_version == "2.1.207"


def test_md_fence_survives_backticks_in_content(tmp_path, fake_repo):
    cwd = str(fake_repo)
    lines = [
        user_text(0, cwd, "hello"),
        _line(1, "summary", cwd, summary="use ``` to fence code"),
    ]
    parsed, comps, att = _att(tmp_path, fake_repo, lines)
    agg = aggregate_session(parsed, att, comps, Config())
    md = render_md(agg, Config())
    # the fence must be longer than any backtick run in the body, or content escapes the block
    assert "````" in md
    assert md.rstrip().endswith("````")
