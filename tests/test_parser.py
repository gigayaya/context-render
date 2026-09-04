"""parser tests: golden event stream, unknown-type injection, ts out-of-order, compaction fixture."""

import json
from datetime import UTC
from pathlib import Path

from context_render.parser import parse_file
from tests.conftest import _line, make_transcript, user_text


def _write(tmp_path, lines, name="00000000-0000-0000-0000-000000000001.jsonl"):
    p = tmp_path / name
    p.write_text(make_transcript(tmp_path, lines), encoding="utf-8")
    return p


def test_golden_event_stream(tmp_path, fake_repo, rich_session_lines):
    p = _write(tmp_path, rich_session_lines)
    ps = parse_file(p)
    kinds = [e.kind for e in ps.events]
    assert ps.parse_status == "ok"
    assert ps.cc_version == "2.1.207"
    assert ps.cwd == str(fake_repo)
    assert "compaction" in kinds
    assert "hook" in kinds
    assert kinds.count("tool_use") == 10
    assert "summary" in kinds
    # tool_use and tool_result paired by id
    tu = next(e for e in ps.events if e.kind == "tool_use" and e.tool_name == "Skill")
    tr = [e for e in ps.events if e.kind == "tool_result" and e.tool_use_id == tu.tool_use_id]
    assert tr and not tr[0].is_error


def test_event_ts_normalized_to_utc(tmp_path, fake_repo):
    """Non-UTC offsets are normalized at parse so DB started_at strings compare consistently."""
    from datetime import datetime

    lines = [user_text(0, str(fake_repo), "hi", timestamp="2026-07-11T22:00:00.000+08:00")]
    ps = parse_file(_write(tmp_path, lines))
    ev = ps.events[0]
    assert ev.ts == datetime(2026, 7, 11, 14, 0, tzinfo=UTC)
    assert ev.ts.isoformat().endswith("+00:00")


def test_index_tool_results_first_wins(tmp_path, fake_repo):
    from context_render.parser.loader import index_tool_results
    from tests.conftest import assistant, tool_result, tool_use

    cwd = str(fake_repo)
    ps = parse_file(_write(tmp_path, [
        assistant(0, cwd, [tool_use("Read", {"file_path": "/x"}, "t1")]),
        tool_result(1, cwd, "t1", "first"),
        tool_result(2, cwd, "t1", "second"),
        tool_result(3, cwd, "t2", "other"),
    ]))
    results = index_tool_results(ps.events)
    assert results["t1"].text == "first" and results["t1"].idx == 1
    assert results["t2"].text == "other"


def test_cc_2_1_25x_bookkeeping_types_are_known_aux(tmp_path, fake_repo):
    """Session-level bookkeeping lines observed in cc 2.1.251–2.1.260 (2026-09-05)
    are auxiliary: kind=system, never degraded."""
    cwd = str(fake_repo)
    lines = [
        user_text(0, cwd, "hi"),
        _line(1, "atis-latch", cwd, atis=""),
        _line(2, "bridge-session", cwd, bridgeSessionId="cse_x", lastSequenceNum=0),
        _line(3, "cost-state", cwd, totalCostUSD=0.5, modelUsage={}),
        _line(4, "file-history-delta", cwd, messageId="m", snapshotMessageId="s",
              trackingPath="/x", backup={}),
    ]
    ps = parse_file(_write(tmp_path, lines))
    assert ps.parse_status == "ok" and ps.unknown_type_counts == {}
    assert [e.kind for e in ps.events] == ["user_msg", "system", "system", "system", "system"]


def test_unknown_type_degrades_not_crash(tmp_path, fake_repo):
    lines = [
        user_text(0, str(fake_repo), "hi"),
        _line(1, "totally-new-event-type", str(fake_repo), payload={"x": 1}),
        _line(2, "another-unknown", str(fake_repo)),
    ]
    ps = parse_file(_write(tmp_path, lines))
    assert ps.parse_status == "degraded"
    assert ps.unknown_type_counts == {"totally-new-event-type": 1, "another-unknown": 1}
    assert len([e for e in ps.events if e.kind == "user_msg"]) == 1


def test_bad_json_line_degrades(tmp_path, fake_repo):
    p = tmp_path / "bad.jsonl"
    p.write_text(
        json.dumps(user_text(0, str(fake_repo), "hi")) + "\n{not json}\n", encoding="utf-8"
    )
    ps = parse_file(p)
    assert ps.bad_line_count == 1
    assert ps.parse_status == "degraded"


def test_ts_out_of_order_flag(tmp_path, fake_repo):
    cwd = str(fake_repo)
    a = user_text(0, cwd, "hi")
    b = user_text(1, cwd, "later")
    b["timestamp"] = "2026-07-11T15:00:00.000Z"
    c = user_text(2, cwd, "way earlier")
    c["timestamp"] = "2026-07-11T13:00:00.000Z"  # 2h regression > tolerance
    ps = parse_file(_write(tmp_path, [a, b, c]))
    assert ps.ts_out_of_order is True
    # small interleaving (parallel tools) does not trigger
    c["timestamp"] = "2026-07-11T14:59:30.000Z"
    ps2 = parse_file(_write(tmp_path, [a, b, c], name="x2.jsonl"))
    assert ps2.ts_out_of_order is False


def test_compaction_known_shapes(tmp_path, fake_repo):
    cwd = str(fake_repo)
    boundary = _line(0, "system", cwd, subtype="compact_boundary")
    summary = user_text(1, cwd, "compacted summary text")
    summary["isCompactSummary"] = True
    ps = parse_file(_write(tmp_path, [boundary, summary]))
    assert [e.kind for e in ps.events] == ["compaction", "compaction"]


def test_missing_ts_tolerated(tmp_path, fake_repo):
    obj = user_text(0, str(fake_repo), "hi")
    del obj["timestamp"]
    ps = parse_file(_write(tmp_path, [obj]))
    assert ps.events[0].ts is None
    assert ps.parse_status == "ok"


def test_mixed_tz_timestamps_do_not_crash(tmp_path, fake_repo):
    """naive timestamps are normalized to UTC-aware, so aware/naive mixes stay comparable."""
    cwd = str(fake_repo)
    a = user_text(0, cwd, "hi")  # Z-suffixed (aware)
    b = user_text(1, cwd, "naive ts")
    b["timestamp"] = "2026-07-11T14:00:30"  # no timezone suffix
    ps = parse_file(_write(tmp_path, [a, b]))
    assert all(e.ts is None or e.ts.tzinfo is not None for e in ps.events)
    assert ps.ts_out_of_order is False


def test_sibling_repo_prefix_not_misattributed(tmp_path, fake_repo):
    """/x/app-api munges to a name that extends /x/app's — its cwd-less files must not leak in."""
    from context_render.parser import discover_sessions
    from context_render.parser.discovery import _munge

    projects = tmp_path / "projects"
    sib = projects / (_munge(fake_repo.resolve()) + "-api")
    sib.mkdir(parents=True)
    (sib / "22222222-aaaa-bbbb-cccc-000000000002.jsonl").write_text(
        json.dumps({"type": "summary", "summary": "no cwd in this file"}) + "\n",
        encoding="utf-8",
    )
    assert discover_sessions(fake_repo, projects) == []


def test_unrelated_project_dir_costs_one_peek(tmp_path, fake_repo, monkeypatch):
    """cwd stays authoritative, but a project dir that cannot belong to this repo must not cost
    one file open per transcript — otherwise every command scales with the user's whole history."""
    from context_render.parser import discovery
    from context_render.parser.discovery import _munge

    def session(dir_: Path, name: str, cwd: Path) -> None:
        dir_.mkdir(parents=True, exist_ok=True)
        (dir_ / name).write_text(
            json.dumps({"type": "user", "cwd": str(cwd), "version": "2.1.207",
                        "message": {"role": "user", "content": "hi"}}) + "\n",
            encoding="utf-8",
        )

    projects = tmp_path / "projects"
    session(projects / _munge(fake_repo.resolve()),
            "44444444-aaaa-bbbb-cccc-000000000004.jsonl", fake_repo)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    other_dir = projects / _munge(elsewhere.resolve())
    for i in range(10):
        session(other_dir, f"5555555{i}-aaaa-bbbb-cccc-00000000000{i}.jsonl", elsewhere)

    opened: list[Path] = []
    real_peek = discovery._peek_cwd
    monkeypatch.setattr(discovery, "_peek_cwd",
                        lambda p, **kw: (opened.append(p), real_peek(p, **kw))[1])

    found = discovery.discover_sessions(fake_repo, projects)
    assert [s.session_id for s in found] == ["44444444-aaaa-bbbb-cccc-000000000004"]
    assert sum(1 for p in opened if p.parent == other_dir) == 1


def test_subdir_project_dir_cwdless_accepted(tmp_path, fake_repo):
    """a project dir munging to an existing repo subdirectory (src/api) still accepts cwd-less files."""
    from context_render.parser import discover_sessions
    from context_render.parser.discovery import _munge

    projects = tmp_path / "projects"
    sub = projects / (_munge(fake_repo.resolve()) + "-src-api")
    sub.mkdir(parents=True)
    (sub / "33333333-aaaa-bbbb-cccc-000000000003.jsonl").write_text(
        json.dumps({"type": "summary", "summary": "no cwd in this file"}) + "\n",
        encoding="utf-8",
    )
    found = discover_sessions(fake_repo, projects)
    assert [s.session_id for s in found] == ["33333333-aaaa-bbbb-cccc-000000000003"]


def test_sidechain_files_merge_chronologically(tmp_path, fake_repo):
    """subagent transcripts (separate files on disk) merge into the parent event
    stream by timestamp, renumbered so the timeline's idx sort stays chronological."""
    from tests.conftest import assistant, tool_result, tool_use, write_sidechain

    cwd = str(fake_repo)
    main = [
        user_text(0, cwd, "review this"),
        assistant(1, cwd, [tool_use("Agent", {"subagent_type": "code-reviewer",
                                              "prompt": "go"}, "t1")]),
        tool_result(9, cwd, "t1", "verdict"),
    ]
    side = [
        user_text(3, cwd, "you are the reviewer"),
        assistant(5, cwd, [tool_use("Read", {"file_path": f"{fake_repo}/docs/conventions.md"},
                                    "s1")]),
        tool_result(6, cwd, "s1", "conventions doc"),
    ]
    p = _write(tmp_path, main)
    sp = write_sidechain(p, "a1", side, agent_type="code-reviewer")

    ps = parse_file(p, [sp])
    side_events = [e for e in ps.events if e.is_sidechain]
    assert side_events, "sidechain events must enter the merged stream"
    assert all(e.agent == "code-reviewer" for e in side_events)
    # chronological interleave: dispatch precedes the sidechain, whose events all
    # precede the dispatching tool_result (ts 14:00:09 > sidechain 14:00:03-06)
    dispatch = next(i for i, e in enumerate(ps.events)
                    if e.kind == "tool_use" and e.tool_name == "Agent")
    first_side = next(i for i, e in enumerate(ps.events) if e.is_sidechain)
    assert dispatch < first_side
    assert ps.events[-1].kind == "tool_result" and not ps.events[-1].is_sidechain
    # renumbered idx is monotone over the merged stream (timeline sorts by idx)
    idxs = [e.idx for e in ps.events]
    assert idxs == sorted(idxs)
    assert ps.parse_status == "ok"
    assert ps.cc_version == "2.1.207"


def test_sidechain_agent_label_falls_back_to_agent_id(tmp_path, fake_repo):
    from tests.conftest import write_sidechain

    cwd = str(fake_repo)
    p = _write(tmp_path, [user_text(0, cwd, "hi")])
    sp = write_sidechain(p, "a9", [user_text(2, cwd, "subagent prompt")])  # no meta.json
    ps = parse_file(p, [sp])
    assert [e.agent for e in ps.events if e.is_sidechain] == ["a9"]


def test_parse_without_sidechains_keeps_file_line_idx(tmp_path, fake_repo):
    """no sidechains → idx stays the raw file line number (evidence refs unchanged)."""
    cwd = str(fake_repo)
    lines = [user_text(0, cwd, "hi"), _line(1, "ai-title", cwd), user_text(2, cwd, "again")]
    ps = parse_file(_write(tmp_path, lines))
    assert [e.idx for e in ps.events] == [0, 1, 2]


def test_discover_attaches_sidechains(fake_repo, fake_projects):
    import os

    from context_render.parser import discover_sessions
    from tests.conftest import user_text as ut
    from tests.conftest import write_sidechain

    proj_dir = next(d for d in fake_projects.iterdir() if d.is_dir())
    main = next(proj_dir.glob("*.jsonl"))
    sp = write_sidechain(main, "a1", [ut(2, str(fake_repo), "subagent prompt")])
    os.utime(sp, (main.stat().st_mtime + 100, main.stat().st_mtime + 100))

    found = discover_sessions(fake_repo, fake_projects)
    assert len(found) == 1
    sf = found[0]
    # freshness must cover the whole session unit: a new/updated subagent file
    # alone re-triggers ingest (needs_update compares these two fields)
    assert sf.sidechain_paths == [sp]
    assert sf.size == main.stat().st_size + sp.stat().st_size
    assert sf.mtime == sp.stat().st_mtime
