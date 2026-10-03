"""Stale-copy window extraction."""

from __future__ import annotations

from pathlib import Path

from context_render.attributor.stale import STALE_EXTRACTOR_VERSION, extract_stale
from context_render.parser.loader import parse_file
from tests.conftest import (
    _line,
    assistant,
    make_transcript,
    tool_result,
    tool_use,
    write_sidechain,
)

CWD = "/repo"
ROOT = Path("/repo")


def _parsed(lines, tmp_path, sidechains=()):
    p = tmp_path / "s.jsonl"
    p.write_text(make_transcript(p, lines), encoding="utf-8")
    return parse_file(p, sidechains)


def _read(idx, tid, path, content="x" * 400, **read_kw):
    return [assistant(idx, CWD, [tool_use("Read", {"file_path": path, **read_kw}, tid)]),
            tool_result(idx + 1, CWD, tid, content)]


def _edit(idx, tid, path):
    return [assistant(idx, CWD, [tool_use(
                "Edit", {"file_path": path, "old_string": "a", "new_string": "b"}, tid)]),
            tool_result(idx + 1, CWD, tid, "ok")]


def _multiedit(idx, tid, path):
    return [assistant(idx, CWD, [tool_use(
                "MultiEdit", {"file_path": path,
                              "edits": [{"old_string": "a", "new_string": "b"}]}, tid)]),
            tool_result(idx + 1, CWD, tid, "ok")]


def _bash(idx, tid, cmd, res="ok"):
    return [assistant(idx, CWD, [tool_use("Bash", {"command": cmd}, tid)]),
            tool_result(idx + 1, CWD, tid, res)]


def test_version_constant():
    assert STALE_EXTRACTOR_VERSION == 1


def test_case1_read_edit_reread_closes_exact(tmp_path):
    lines = (_read(0, "t1", "/repo/a.md") + _edit(2, "t2", "/repo/a.md")
             + _read(4, "t3", "/repo/a.md"))
    (w,) = extract_stale(_parsed(lines, tmp_path), ROOT)
    assert (w.path, w.read_idx, w.mutate_idx, w.close_idx) == ("a.md", 0, 2, 4)
    assert w.outcome == "re-read" and w.confidence == "exact"
    assert w.mutate_tool == "Edit" and w.window_agent == "" and not w.window_side
    assert w.read_tokens_est == 100  # ceil(400/4)
    assert not w.read_partial


def test_case2_never_reread_at_session_end(tmp_path):
    lines = _read(0, "t1", "/repo/a.md") + _edit(2, "t2", "/repo/a.md")
    (w,) = extract_stale(_parsed(lines, tmp_path), ROOT)
    assert w.outcome == "never-re-read" and w.close_idx is None and w.confidence == "exact"


def test_case3_compaction_closes_and_resets(tmp_path):
    lines = (_read(0, "t1", "/repo/a.md") + _edit(2, "t2", "/repo/a.md")
             + [_line(4, "system", CWD, subtype="compact_boundary")]
             + _read(5, "t3", "/repo/a.md"))
    (w,) = extract_stale(_parsed(lines, tmp_path), ROOT)
    # closed by compaction, NOT mistaken for a re-read close by the later Read
    assert w.outcome == "compacted" and w.close_idx == 4
    # and the pre-compaction copy is gone: a mutation after the boundary must
    # only open a window against the post-compaction read
    lines2 = lines + _edit(7, "t4", "/repo/a.md")
    ws = extract_stale(_parsed(lines2, tmp_path), ROOT)
    assert [x.outcome for x in ws] == ["compacted", "never-re-read"]
    assert ws[1].read_idx == 5 and ws[1].mutate_idx == 7


def test_case4_sidechain_edit_stales_main_chain_heuristic(tmp_path):
    main = _read(0, "t1", "/repo/a.md")
    p = tmp_path / "s.jsonl"
    p.write_text(make_transcript(p, main), encoding="utf-8")
    sc = write_sidechain(p, "ag1", _edit(10, "t9", "/repo/a.md"), agent_type="worker")
    parsed = parse_file(p, [sc])
    (w,) = extract_stale(parsed, ROOT)
    assert w.window_agent == "" and not w.window_side  # main chain's copy went stale
    assert w.confidence == "heuristic"  # cross-window trigger
    assert w.outcome == "never-re-read"


def test_case5_bash_sed_i_mutates_grep_sed_n_do_not(tmp_path):
    lines = (_read(0, "t1", "/repo/a.md")
             + _bash(2, "t2", "grep foo /repo/a.md")
             + _bash(4, "t3", "sed -i 's/a/b/' /repo/a.md"))
    (w,) = extract_stale(_parsed(lines, tmp_path), ROOT)
    assert w.mutate_idx == 4 and w.mutate_tool == "sed" and w.confidence == "heuristic"
    # grep alone never opens anything
    only_grep = _read(0, "t1", "/repo/a.md") + _bash(2, "t2", "grep foo /repo/a.md")
    assert extract_stale(_parsed(only_grep, tmp_path), ROOT) == []


def test_case6_wildcard_hits_only_in_context_files(tmp_path):
    lines = _read(0, "t1", "/repo/a.md") + _bash(2, "t2", "git pull")
    ws = extract_stale(_parsed(lines, tmp_path), ROOT)
    assert [(w.path, w.mutate_tool, w.confidence) for w in ws] == [
        ("a.md", "git pull", "heuristic")]


def test_case7_outside_repo_paths_ignored(tmp_path):
    lines = (_read(0, "t1", "/elsewhere/x.md") + _edit(2, "t2", "/elsewhere/x.md"))
    assert extract_stale(_parsed(lines, tmp_path), ROOT) == []


def test_case8_multiple_reads_single_window_last_read_idx(tmp_path):
    lines = (_read(0, "t1", "/repo/a.md") + _read(2, "t2", "/repo/a.md")
             + _edit(4, "t3", "/repo/a.md"))
    (w,) = extract_stale(_parsed(lines, tmp_path), ROOT)
    assert w.read_idx == 2 and w.mutate_idx == 4


def test_second_mutation_does_not_reopen(tmp_path):
    lines = (_read(0, "t1", "/repo/a.md") + _edit(2, "t2", "/repo/a.md")
             + _edit(4, "t3", "/repo/a.md"))
    (w,) = extract_stale(_parsed(lines, tmp_path), ROOT)
    assert w.mutate_idx == 2  # first trigger holds; no second window


def test_partial_read_flag_recorded_not_judged(tmp_path):
    lines = (_read(0, "t1", "/repo/a.md", offset=10, limit=50)
             + _edit(2, "t2", "/repo/a.md"))
    (w,) = extract_stale(_parsed(lines, tmp_path), ROOT)
    assert w.read_partial and w.outcome == "never-re-read"


def test_failed_tool_results_produce_no_events(tmp_path):
    lines = [assistant(0, CWD, [tool_use("Read", {"file_path": "/repo/a.md"}, "t1")]),
             tool_result(1, CWD, "t1", "err", is_error=True), *_edit(2, "t2", "/repo/a.md")]
    assert extract_stale(_parsed(lines, tmp_path), ROOT) == []


def test_write_to_unread_path_is_noop(tmp_path):
    lines = [assistant(0, CWD, [tool_use(
                 "Write", {"file_path": "/repo/new.md", "content": "x"}, "t1")]),
             tool_result(1, CWD, "t1", "ok")]
    assert extract_stale(_parsed(lines, tmp_path), ROOT) == []


def test_multiedit_opens_window_exact(tmp_path):
    lines = _read(0, "t1", "/repo/a.md") + _multiedit(2, "t2", "/repo/a.md")
    (w,) = extract_stale(_parsed(lines, tmp_path), ROOT)
    assert w.mutate_tool == "MultiEdit" and w.confidence == "exact"
    assert w.outcome == "never-re-read"


def test_dotdot_bash_read_and_edit_unify_with_clean_form(tmp_path):
    # a bash read via a `..` path and a clean-form Edit/Read must resolve to
    # the same window key (Fix 3: path normalization, AC2a false-positive guard)
    lines = (_bash(0, "t1", "cat docs/../a.md")
             + _edit(2, "t2", "/repo/a.md")
             + _read(4, "t3", "/repo/a.md"))
    (w,) = extract_stale(_parsed(lines, tmp_path), ROOT)
    assert w.path == "a.md" and w.outcome == "re-read" and w.close_idx == 4
