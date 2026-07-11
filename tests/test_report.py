"""Report-layer tests (AC3/AC9): timeline golden, two-form consistency, CJK alignment, MISS, aggregate status."""

from context_render.attributor import attribute
from context_render.config import Config
from context_render.inventory.scanner import scan_components, write_manifest
from context_render.parser import parse_file
from context_render.pipeline import scan_repo
from context_render.report.aggregate import _attach_ctx_tokens, aggregate_session, aggregate_window
from context_render.report.charts import daily_histogram, display_width, hbar_chart, pad_to
from context_render.report.render_md import render_md
from context_render.report.render_term import render_term, session_lines, window_lines
from context_render.report.timeline import render_timeline_lines
from context_render.store import Store
from tests.conftest import make_transcript


def _by_prefix(rows, prefix):
    return next(rows[k] for k in rows if k.startswith(prefix))


def _session_agg(tmp_path, fake_repo, rich_session_lines, evidence=False):
    p = tmp_path / "s.jsonl"
    p.write_text(make_transcript(fake_repo, rich_session_lines), encoding="utf-8")
    parsed = parse_file(p)
    comps = [c for c in scan_components(fake_repo) if c.provenance == "local"]
    att = attribute(parsed, comps, fake_repo)
    return aggregate_session(parsed, att, comps, Config(), include_evidence=evidence)


def test_timeline_golden(tmp_path, fake_repo, rich_session_lines):
    agg = _session_agg(tmp_path, fake_repo, rich_session_lines)
    tl = agg["timeline"]
    kinds = [t["kind"] for t in tl]
    assert kinds[0] == "session_start"
    assert kinds[-1] == "session_end"
    assert "compaction" in kinds  # self-made fixture serves as golden (spike #11)
    body = render_term(agg, Config())
    assert "⟐  compaction" in body
    assert "session start (cc 2.1.207)" in body
    assert "[I] skill:db-migrate" in body


def test_two_renderers_consistent(tmp_path, fake_repo, rich_session_lines):
    """Terminal and md forms consistent (AC3): the full md contains every line of the terminal form."""
    agg = _session_agg(tmp_path, fake_repo, rich_session_lines)
    cfg = Config()
    term = session_lines(agg, cfg, full=False)
    md = render_md(agg, cfg)
    for line in term:
        assert line.rstrip() in md, f"terminal line not in md: {line!r}"


def test_miss_detection_session(tmp_path, fake_repo, rich_session_lines):
    agg = _session_agg(tmp_path, fake_repo, rich_session_lines)
    rows = {r["id"]: r for r in agg["components"]}
    # PreToolUse-Bash hook: session has a commit but not triggered → MISS ⚠
    assert _by_prefix(rows, "hook:PreToolUse-Bash-")["miss"] is True
    assert _by_prefix(rows, "hook:PostToolUse-Edit-")["miss"] is False  # was triggered


def test_evidence_downdrill(tmp_path, fake_repo, rich_session_lines):
    agg = _session_agg(tmp_path, fake_repo, rich_session_lines, evidence=True)
    rows = {r["id"]: r for r in agg["components"]}
    assert any(e["event_idx"] is not None for e in rows["mcp:playwright"]["evidence"])


def test_unsupported_version_downgrades_session_report(tmp_path, fake_repo, rich_session_lines):
    """Regression (AB review): the A11 downgrade (unsupported version → all heuristic) applied
    only on the DB write path, so `last` showed exact confidence and no warning while
    `report` marked the very same session heuristic."""
    for ln in rich_session_lines:
        ln["version"] = "9.9.9"
    agg = _session_agg(tmp_path, fake_repo, rich_session_lines)
    assert all(r["confidence"] == "heuristic" for r in agg["components"])
    assert all(f["confidence"] == "heuristic" for f in agg["file_loads"])
    assert any("support matrix" in w for w in agg["warnings"])


def test_window_aggregate_status(fake_repo, fake_projects):
    comps = [c for c in scan_components(fake_repo) if c.provenance == "local"]
    write_manifest(fake_repo, comps)
    cfg = Config()
    scan_repo(fake_repo, cfg, projects_dir=fake_projects)
    store = Store(fake_repo / ".context-render" / "db.sqlite")
    try:
        agg = aggregate_window(store, comps, cfg, since_iso=None, since_label="all history")
    finally:
        store.close()
    rows = {r["id"]: r for r in agg["components"]}
    assert rows["skill:db-migrate"]["status"].startswith("low-use")  # count=1 ≤ 2
    assert rows["mcp:playwright"]["invoked"] == 2
    assert _by_prefix(rows, "hook:PreToolUse-Bash-")["status"].startswith("☠ never triggered")
    assert _by_prefix(rows, "hook:PreToolUse-Bash-")["miss_count"] == 1
    # claude_md root is always L → never falls into deadweight
    assert not rows["claude-md:root"]["status"].startswith("☠")
    assert agg["deadweight"]["tokens"] >= 0
    assert agg["window"]["session_count"] == 1
    # built-in hint (MUST): root CLAUDE.md prescription
    assert any("moved into a skill" in h for h in agg["hints"])
    lines = window_lines(agg, cfg, full=True)
    assert any("Deadweight total" in ln for ln in lines)


def test_empty_window_suppresses_deadweight_verdict(fake_repo):
    """Regression: with zero ingested sessions the report declared every component
    ☠ deadweight (100%) and persisted that verdict to --md with only a stderr note."""
    comps = [c for c in scan_components(fake_repo) if c.provenance == "local"]
    cfg = Config()
    store = Store(fake_repo / ".context-render" / "db.sqlite")  # empty: nothing scanned
    try:
        agg = aggregate_window(store, comps, cfg, since_iso=None, since_label="30d")
    finally:
        store.close()
    assert agg["window"]["session_count"] == 0
    assert all(r["status"] == "no data" for r in agg["components"])
    assert agg["deadweight"]["tokens"] == 0
    assert agg["deadweight"]["pct"] == 0.0
    assert not agg["hints"]  # usage hints presume usage data
    assert any("no ingested sessions" in w for w in agg["warnings"])
    # the same line builders feed term and --md, so the body itself must carry the warning
    body = "\n".join(window_lines(agg, cfg, full=True))
    assert "no ingested sessions" in body
    assert "☠" not in body
    assert "Deadweight total" not in body


def test_cjk_alignment():
    # CJK width support is a feature; these literals stay CJK to exercise that code path.
    assert display_width("中文ab") == 6
    assert len(pad_to("中文", 10)) == 8  # 2 chars + 6 spaces; display width 10
    assert display_width(pad_to("中文", 10)) == 10
    chart = hbar_chart([("中文標籤元件", 100.0), ("ascii-label", 50.0)])
    # both bar rows start at the same column (equal display width)
    starts = [display_width(ln.split("▇")[0]) for ln in chart]
    assert starts[0] == starts[1]


def test_daily_histogram_column_chart():
    """Sessions/day render as vertical columns on one shared baseline (aligned, scaled)."""
    days = [
        ("06-12", 13), ("06-13", 0), ("06-14", 0), ("06-15", 19),
        ("06-16", 17), ("06-17", 13), ("06-18", 9), ("06-19", 0),
        ("06-20", 9), ("06-21", 2), ("06-22", 7), ("06-23", 6),
        ("06-24", 2), ("06-25", 13), ("06-26", 12), ("06-27", 0),
        ("06-28", 0), ("06-29", 16), ("06-30", 13), ("07-01", 10),
        ("07-02", 9), ("07-03", 0), ("07-04", 0), ("07-05", 0),
        ("07-06", 3), ("07-07", 5), ("07-08", 6), ("07-09", 7),
        ("07-10", 2),
    ]
    lines = daily_histogram(days, height=4)
    assert len(lines) == 6  # 4 plot rows + axis + date labels
    top, *_, bottom_row, axis, labels = lines
    # y-axis anchors: max at top, 0 at the baseline
    assert top.lstrip().startswith("19")
    assert axis.lstrip().startswith("0 └")
    # the busiest day (idx 3) reaches full height; every column sits at a fixed offset
    plot_start = axis.index("└") + 2
    col = plot_start + 3 * 2  # two columns per day (bar + gap)
    assert top[col] == "█"
    # zero days show a faint dot on the baseline row, not a bar
    assert bottom_row[plot_start + 1 * 2] == "·"
    # a small day inks only the baseline row: nothing in the top row at that column
    assert top[plot_start + 9 * 2] == " "  # 06-21 = 2 sessions
    assert bottom_row[plot_start + 9 * 2] != " "
    # weekly ticks carry dates under the axis
    assert "06-12" in labels and "06-19" in labels and "07-03" in labels
    # every plot row is anchored to the same axis column
    assert {ln[axis.index("└")] for ln in lines[:4]} == {"│"}


def test_daily_histogram_paint_hits_marks_only():
    days = [("06-12", 2), ("06-13", 0)]
    lines = daily_histogram(days, height=2, bar_paint=lambda s: f"<{s}>")
    body = "\n".join(lines)
    assert "<" in body  # bars painted
    assert "<·>" not in body and "< >" not in body  # dots and blanks stay plain


def test_daily_histogram_clips_to_width():
    days = [(f"{m:02d}-{d:02d}", 1) for m in (1, 2) for d in range(1, 29)]  # 56 days
    lines = daily_histogram(days, height=3, total_width=40)
    assert any("…" in ln for ln in lines)  # clipping is disclosed
    assert all(display_width(ln) <= 40 for ln in lines)


def test_deadweight_focus(fake_repo, fake_projects):
    comps = [c for c in scan_components(fake_repo) if c.provenance == "local"]
    write_manifest(fake_repo, comps)
    cfg = Config()
    scan_repo(fake_repo, cfg, projects_dir=fake_projects)
    store = Store(fake_repo / ".context-render" / "db.sqlite")
    try:
        agg = aggregate_window(store, comps, cfg, since_iso=None,
                               since_label="last 90d", deadweight_only=True)
    finally:
        store.close()
    assert all(r["dead"] for r in agg["components"])
    # window <90d or sessions <20 → widen-observation-window hint (MUST)
    assert any("widening the observation window" in h for h in agg["hints"])


def test_file_loads_order_and_display(tmp_path, fake_repo, rich_session_lines):
    """File loads: order, counts, component links, timeline [read] rows (non-component files)."""
    agg = _session_agg(tmp_path, fake_repo, rich_session_lines)
    loads = agg["file_loads"]
    paths = [f["path"] for f in loads]
    # by context injection order: root CLAUDE.md (system-injected, always first) → conventions
    # (Read+Bash aggregated by same path) → guidelines → subdirectory CLAUDE.md
    assert paths == ["CLAUDE.md", "docs/conventions.md", "guidelines/INDEX.md",
                     "src/api/CLAUDE.md"]
    root = loads[0]
    assert root["order"] == 1
    assert root["tools"] == {"system-injected": 1}
    assert root["component"] == "claude-md:root"
    conv = loads[1]
    assert conv["order"] == 2
    assert conv["count"] == 2  # Read ×1 + Bash ×1
    assert conv["tools"] == {"Read": 1, "Bash": 1}
    assert conv["confidence"] == "heuristic"  # Bash file read mixed in → heuristic
    # file:-type components are added to the manifest manually by the user; not added → no component link but still fully recorded
    assert conv["component"] is None
    gl = loads[2]
    assert gl["component"] is None  # non-manifest components are recorded too
    assert gl["confidence"] == "exact"
    sub = loads[3]
    assert sub["component"] == "claude-md:src-api"  # manifest component file → linked component id

    body = render_term(agg, Config())
    assert "File loads (context injection order, 4 files)" in body
    assert "guidelines/INDEX.md" in body
    # non-component files enter the timeline; component files already have an [L] transition, not duplicated
    assert "[read] Read guidelines/INDEX.md" in body.replace("  ", " ") or \
        any("[read]" in ln and "guidelines/INDEX.md" in ln for ln in body.splitlines())


def test_failed_read_not_in_file_loads(tmp_path, fake_repo):
    from tests.conftest import assistant, tool_result, tool_use

    cwd = str(fake_repo)
    lines = [
        assistant(0, cwd, [tool_use("Read", {"file_path": f"{fake_repo}/nope.md"}, "t1")]),
        tool_result(1, cwd, "t1", "File does not exist", is_error=True),
    ]
    agg = _session_agg(tmp_path, fake_repo, lines)
    # a failed Read never entered context, not recorded; only the system-injected root CLAUDE.md remains
    assert [f["path"] for f in agg["file_loads"]] == ["CLAUDE.md"]


def _reads_agg(tmp_path, fake_repo, read_inputs):
    """Session made only of successful Read tool_use events with the given inputs."""
    from tests.conftest import assistant, tool_result, tool_use

    cwd = str(fake_repo)
    lines = []
    for i, (tool, inp) in enumerate(read_inputs):
        lines.append(assistant(i * 2, cwd, [tool_use(tool, inp, f"t{i}")]))
        lines.append(tool_result(i * 2 + 1, cwd, f"t{i}", "content"))
    return _session_agg(tmp_path, fake_repo, lines)


def _load_line(agg, path_frag, full=False):
    return next(ln for ln in session_lines(agg, Config(), full=full) if path_frag in ln)


def test_read_line_ranges_exact(tmp_path, fake_repo):
    """Read offset/limit → per-load line range (notation A: colon prefix, en-dash)."""
    agg = _reads_agg(tmp_path, fake_repo, [
        ("Read", {"file_path": f"{fake_repo}/docs/conventions.md", "offset": 80, "limit": 81}),
        ("Read", {"file_path": f"{fake_repo}/guidelines/INDEX.md", "offset": 159}),
        ("Read", {"file_path": f"{fake_repo}/README.md", "limit": 100}),
    ])
    by_path = {f["path"]: f for f in agg["file_loads"]}
    assert by_path["docs/conventions.md"]["ranges"] == [[80, 160]]  # offset+limit → closed
    assert by_path["guidelines/INDEX.md"]["ranges"] == [[159, None]]  # offset only → open end
    assert by_path["README.md"]["ranges"] == [[1, 100]]  # limit only → from line 1
    assert ":80–160" in _load_line(agg, "docs/conventions.md")
    assert ":159–" in _load_line(agg, "guidelines/INDEX.md")
    assert ":1–100" in _load_line(agg, "README.md")


def test_whole_file_reads_stay_unmarked(tmp_path, fake_repo):
    """No offset/limit → no range mark, even across repeats (Read caps at 2000 lines,
    so ':all' would overstate what entered context)."""
    tgt = f"{fake_repo}/docs/conventions.md"
    agg = _reads_agg(tmp_path, fake_repo, [
        ("Read", {"file_path": tgt}),
        ("Read", {"file_path": tgt}),
    ])
    entry = next(f for f in agg["file_loads"] if f["path"] == "docs/conventions.md")
    assert entry["ranges"] == [None, None]
    ln = _load_line(agg, "docs/conventions.md")
    assert ":all" not in ln and "–" not in ln


def test_mixed_ranges_mark_whole_read_as_all(tmp_path, fake_repo):
    """Ranged + whole reads of one file → whole read renders as :all to keep read order
    legible; Bash reads of the same file contribute no range token."""
    tgt = f"{fake_repo}/docs/conventions.md"
    agg = _reads_agg(tmp_path, fake_repo, [
        ("Read", {"file_path": tgt, "offset": 80, "limit": 81}),
        ("Read", {"file_path": tgt, "offset": 410, "limit": 51}),
        ("Read", {"file_path": tgt}),
        ("Bash", {"command": "cat docs/conventions.md"}),
    ])
    entry = next(f for f in agg["file_loads"] if f["path"] == "docs/conventions.md")
    assert entry["ranges"] == [[80, 160], [410, 460], None]
    assert ":80–160 :410–460 :all" in _load_line(agg, "docs/conventions.md")


def test_range_overflow_folds_to_plus_n(tmp_path, fake_repo):
    """More than 4 ranges → first 4 shown + '+n' in default view; --full shows all."""
    tgt = f"{fake_repo}/docs/conventions.md"
    agg = _reads_agg(tmp_path, fake_repo, [
        ("Read", {"file_path": tgt, "offset": n, "limit": 1}) for n in (10, 20, 30, 40, 50, 60)
    ])
    short = _load_line(agg, "docs/conventions.md")
    assert ":10–10 :20–20 :30–30 :40–40 +2" in short
    assert ":50–50" not in short
    full = _load_line(agg, "docs/conventions.md", full=True)
    assert ":50–50 :60–60" in full
    assert "+2" not in full


def test_timeline_actions(tmp_path, fake_repo, rich_session_lines):
    """timeline action events: Edit/Write/Bash first-class display; failed actions marked."""
    agg = _session_agg(tmp_path, fake_repo, rich_session_lines)
    actions = [t for t in agg["timeline"] if t["kind"] == "action"]
    tools = [a["tool"] for a in actions]
    assert "Edit" in tools and "Bash" in tools
    edit = next(a for a in actions if a["tool"] == "Edit")
    assert edit["detail"] == "src/api/main.py"
    bash = next(a for a in actions if "git commit" in a["detail"])
    assert bash["tool"] == "Bash"

    body = render_term(agg, Config())
    assert "[edit]" in body and "[bash]" in body
    assert "src/api/main.py" in body


def test_unused_components_compact(tmp_path, fake_repo):
    """Unused components collapse into one dim block instead of one row each."""
    from tests.conftest import assistant, tool_result, tool_use

    cwd = str(fake_repo)
    lines = [
        assistant(0, cwd, [tool_use("Read", {"file_path": f"{fake_repo}/docs/conventions.md"}, "t1")]),
        tool_result(1, cwd, "t1", "content"),
    ]
    agg = _session_agg(tmp_path, fake_repo, lines)
    body = render_term(agg, Config())
    # one compact label, not one per row
    assert body.count("may be irrelevant to this task") == 1
    assert "not loaded (" in body
    assert "not triggered (2 hooks)" in body
    # every unused id still listed
    assert "skill:db-migrate" in body and "command:release" in body


def test_context_map(tmp_path, fake_repo, rich_session_lines):
    """Context-window map: ▼ loads above, ▲ actions below, one event-block lane each."""
    agg = _session_agg(tmp_path, fake_repo, rich_session_lines)
    body = render_term(agg, Config())
    assert "context window map" in body
    assert "▼" in body and "▲" in body
    assert "⟐" in body.split("timeline")[0]  # compaction marked inside the bar too
    lines = body.splitlines()
    top = next(i for i, ln in enumerate(lines) if "┌" in ln)
    bottom = next(i for i, ln in enumerate(lines) if "└" in ln)
    assert bottom - top == 3  # exactly two lanes inside the event box
    assert lines[top + 1].endswith("▼ injected")
    assert lines[top + 2].endswith("▲ actions")
    # both lanes carry event blocks sized by est_tokens
    fill_chars = set("▁▂▃▄▅▆▇█")
    assert fill_chars & set(lines[top + 1]) and fill_chars & set(lines[top + 2])
    # second bar: window occupancy (USAGE fixture = 10k prompt tokens vs 200k window)
    occ = next(ln for ln in lines if ln.startswith("  window"))
    assert "occupancy · peak 10k/200k tok" in occ
    assert fill_chars & set(occ)
    cfg = Config()
    cfg.graph = False
    assert "context window map" not in render_term(agg, cfg)


def test_context_map_labels_match_timeline_numbers(tmp_path, fake_repo, rich_session_lines):
    """Arrow labels in the map are the row numbers of the timeline listing."""
    agg = _session_agg(tmp_path, fake_repo, rich_session_lines)
    body = render_term(agg, Config()).splitlines()
    # timeline listing rows are numbered by position
    tl_start = next(i for i, ln in enumerate(body) if ln.strip() == "timeline")
    first = body[tl_start + 1].strip()
    assert first.startswith("1 ") and "session start" in first
    # collect load numbers from the map's label lanes (between map head and arrow row)
    map_head = next(i for i, ln in enumerate(body) if "context window map" in ln)
    arrow_row = next(i for i, ln in enumerate(body) if ln.startswith("  loads"))
    lane_nos: set[int] = set()
    for ln in body[map_head + 1:arrow_row]:
        for token in ln.split():
            if token.isdigit():
                lane_nos.add(int(token))
    assert lane_nos, "at least one load label placed"
    # every label points at a load-like row in the timeline listing
    tl_rows = [ln.strip() for ln in body[tl_start + 1:]]
    for no in lane_nos:
        row = tl_rows[no - 1]
        assert row.startswith(f"{no} ")
        assert "[read]" in row or "[L]" in row


def test_context_map_block_heights_scale(tmp_path, fake_repo, rich_session_lines):
    """Bigger events draw taller blocks (√-compressed against the session max)."""
    agg = _session_agg(tmp_path, fake_repo, rich_session_lines)
    tl = agg["timeline"]
    assert any(e.get("est_tokens", 0) > 0 for e in tl), "per-event token estimates present"
    body = render_term(agg, Config()).splitlines()
    top = next(i for i, ln in enumerate(body) if "┌" in ln)
    lanes = body[top + 1] + body[top + 2]
    levels = {ch for ch in lanes if ch in "▁▂▃▄▅▆▇█"}
    assert "█" in levels  # the largest event reaches full height
    assert len(levels) >= 2  # smaller events stay distinguishable


def test_context_map_occupancy_snaps_to_known_window_tier():
    """Peak above the configured window is proof of a bigger window: the denominator
    snaps up to the smallest known tier (1M), never to the peak itself."""
    from context_render.report.context_map import context_map_lines

    timeline = [
        {"kind": "file_read", "evidence_ref": 0, "est_tokens": 500, "ts": None},
        {"kind": "action", "evidence_ref": 5, "est_tokens": 100, "ts": None},
    ]
    samples = [{"idx": 0, "tokens": 150_000}, {"idx": 5, "tokens": 612_887}]
    body = "\n".join(context_map_lines(timeline, samples, window_tokens=200_000))
    assert "peak 612.9k/1M tok" in body
    # beyond every known tier → fall back to the peak (occupancy still renders)
    huge = [{"idx": 0, "tokens": 1_500_000}]
    body = "\n".join(context_map_lines(timeline, huge, window_tokens=200_000))
    assert "peak 1.5M/1.5M tok" in body


def test_context_map_minimal_session(tmp_path, fake_repo):
    """A tiny session (no usage fields, one read) still renders the map."""
    from tests.conftest import assistant, tool_result, tool_use

    cwd = str(fake_repo)
    lines = [
        assistant(0, cwd, [tool_use("Read", {"file_path": f"{fake_repo}/docs/conventions.md"}, "t1")]),
        tool_result(1, cwd, "t1", "content"),
    ]
    agg = _session_agg(tmp_path, fake_repo, lines)
    assert agg["context_samples"] == []
    body = render_term(agg, Config())
    assert "context window map" in body
    assert "▼ injected" in body and "▲ actions" in body
    assert "occupancy" not in body  # no usage data → no occupancy bar


def test_term_plain_by_default_and_style_strips_to_plain(tmp_path, fake_repo, rich_session_lines):
    """No ANSI without a tty; styled output minus escape codes equals the plain form."""
    import re

    from context_render.report.ansi import Style

    agg = _session_agg(tmp_path, fake_repo, rich_session_lines)
    cfg = Config()
    plain = session_lines(agg, cfg, full=False)
    assert not any("\x1b[" in ln for ln in plain)
    styled = session_lines(agg, cfg, full=False, style=Style(True))
    assert any("\x1b[" in ln for ln in styled)
    stripped = [re.sub(r"\x1b\[[0-9;]*m", "", ln) for ln in styled]
    assert stripped == plain


def test_render_term_full_untruncates(tmp_path, fake_repo, rich_session_lines):
    """render_term full=True: complete report in the terminal path (no truncation hints)."""
    agg = _session_agg(tmp_path, fake_repo, rich_session_lines)
    cfg = Config(timeline_term_max=2)
    assert "truncated" in render_term(agg, cfg)  # default path still truncates
    full = render_term(agg, cfg, full=True)
    assert "truncated" not in full
    # Every timeline entry is rendered
    tl_start = full.index("timeline")
    assert full[tl_start:].count("─") >= len(agg["timeline"])


def test_timeline_failed_action_marked(tmp_path, fake_repo):
    from tests.conftest import assistant, tool_result, tool_use

    cwd = str(fake_repo)
    lines = [
        assistant(0, cwd, [tool_use("Bash", {"command": "make deploy"}, "t1")]),
        tool_result(1, cwd, "t1", "make: *** No rule", is_error=True),
    ]
    agg = _session_agg(tmp_path, fake_repo, lines)
    action = next(t for t in agg["timeline"] if t["kind"] == "action")
    assert action["detail"].endswith(" (failed)")


def test_sidechain_separated_in_report_layer(tmp_path, fake_repo):
    """subagent windows are separate: their reads stay out of file_loads (context injection
    order) and context_samples; the timeline still carries them, flagged."""
    from tests.conftest import USAGE, assistant, tool_result, tool_use, user_text, write_sidechain

    cwd = str(fake_repo)
    main = [
        user_text(0, cwd, "go"),
        assistant(1, cwd, [tool_use("Read", {"file_path": f"{fake_repo}/docs/conventions.md"},
                                    "t1")], usage=USAGE),
        tool_result(2, cwd, "t1", "doc"),
        assistant(3, cwd, [tool_use("Agent", {"subagent_type": "code-reviewer",
                                              "prompt": "go"}, "t2")], usage=USAGE),
        tool_result(9, cwd, "t2", "done"),
    ]
    side = [
        assistant(5, cwd, [tool_use("Read", {"file_path": f"{fake_repo}/guidelines/INDEX.md"},
                                    "s1")], usage=USAGE),
        tool_result(6, cwd, "s1", "idx"),
    ]
    p = tmp_path / "s.jsonl"
    p.write_text(make_transcript(fake_repo, main), encoding="utf-8")
    sp = write_sidechain(p, "a1", side, agent_type="code-reviewer")
    parsed = parse_file(p, [sp])
    comps = [c for c in scan_components(fake_repo) if c.provenance == "local"]
    att = attribute(parsed, comps, fake_repo)
    agg = aggregate_session(parsed, att, comps, Config())

    paths = [f["path"] for f in agg["file_loads"]]
    assert "docs/conventions.md" in paths
    assert "guidelines/INDEX.md" not in paths  # read into the subagent's window, not this one
    assert len(agg["context_samples"]) == 2  # two main-chain assistant messages only
    side_rows = [t for t in agg["timeline"] if t.get("sidechain")]
    assert side_rows and any("guidelines/INDEX.md" in t["detail"] for t in side_rows)


def test_context_map_skips_sidechain_rows():
    from context_render.report.context_map import context_map_lines

    tl = [
        {"kind": "file_read", "detail": "Read a.md", "evidence_ref": 1,
         "est_tokens": 10, "ts": None, "confidence": "exact"},
        {"kind": "file_read", "detail": "[subagent:x] Read b.md", "evidence_ref": 2,
         "est_tokens": 10, "ts": None, "confidence": "exact", "sidechain": True},
    ]
    joined = "\n".join(context_map_lines(tl, []))
    # ▼ appears once in the legend, once per main-window load column, once as the
    # event-lane annotation; the sidechain load must not add a fourth
    assert joined.count("▼") == 3


def test_attach_ctx_tokens_carry_forward():
    """Basic carry-forward: a row's value = the nearest sample at or before its idx; None before the first sample."""
    tl = [
        {"evidence_ref": 1, "kind": "session_start"},
        {"evidence_ref": 3, "kind": "file_read"},
        {"evidence_ref": 5, "kind": "action"},
        {"evidence_ref": 9, "kind": "file_read"},
    ]
    samples = [{"idx": 4, "tokens": 21_000}, {"idx": 8, "tokens": 24_000}]
    _attach_ctx_tokens(tl, samples)
    assert [t["ctx_tokens"] for t in tl] == [None, None, 21_000, 24_000]


def test_attach_ctx_tokens_inclusive_same_idx():
    """"At or before" is inclusive: a row sharing the sample's idx (tool_use of the same assistant message) sees that sample."""
    tl = [{"evidence_ref": 4, "kind": "action"}]
    _attach_ctx_tokens(tl, [{"idx": 4, "tokens": 10_000}])
    assert tl[0]["ctx_tokens"] == 10_000


def test_attach_ctx_tokens_compaction_resets():
    """The compaction row itself and rows after it are None until the next sample (pre-compaction numbers are known-wrong)."""
    tl = [
        {"evidence_ref": 5, "kind": "action"},
        {"evidence_ref": 6, "kind": "compaction"},
        {"evidence_ref": 7, "kind": "action"},
        {"evidence_ref": 10, "kind": "action"},
    ]
    samples = [{"idx": 4, "tokens": 150_000}, {"idx": 9, "tokens": 30_000}]
    _attach_ctx_tokens(tl, samples)
    assert [t["ctx_tokens"] for t in tl] == [150_000, None, None, 30_000]


def test_attach_ctx_tokens_sidechain_none():
    """Sidechain rows live in the subagent's own window → always None, and they don't affect the main-chain carry."""
    tl = [
        {"evidence_ref": 5, "kind": "action", "sidechain": True},
        {"evidence_ref": 6, "kind": "action"},
    ]
    _attach_ctx_tokens(tl, [{"idx": 4, "tokens": 12_000}])
    assert tl[0]["ctx_tokens"] is None
    assert tl[1]["ctx_tokens"] == 12_000


def test_attach_ctx_tokens_no_samples():
    """Transcript has no usage at all → whole column None, no error raised."""
    tl = [{"evidence_ref": 1, "kind": "session_start"}]
    _attach_ctx_tokens(tl, [])
    assert tl[0]["ctx_tokens"] is None


def test_attach_ctx_tokens_list_order_independent():
    """Decided by event idx, independent of list order (display order of ts-out-of-order sessions doesn't affect the join)."""
    tl = [
        {"evidence_ref": 7, "kind": "action"},
        {"evidence_ref": 2, "kind": "file_read"},
    ]
    _attach_ctx_tokens(tl, [{"idx": 5, "tokens": 9_000}])
    assert tl[0]["ctx_tokens"] == 9_000
    assert tl[1]["ctx_tokens"] is None


def test_aggregate_timeline_has_ctx_tokens(tmp_path, fake_repo, rich_session_lines):
    """Every timeline dict produced by aggregate_session carries ctx_tokens, and the numbers can only be measured sample values."""
    agg = _session_agg(tmp_path, fake_repo, rich_session_lines)
    assert all("ctx_tokens" in t for t in agg["timeline"])
    measured = [t["ctx_tokens"] for t in agg["timeline"] if t["ctx_tokens"] is not None]
    assert measured  # rich fixture has assistant usage → at least one row has a value
    assert set(measured) <= {s["tokens"] for s in agg["context_samples"]}
    assert all(t["ctx_tokens"] is None for t in agg["timeline"] if t.get("sidechain"))


def test_render_timeline_ctx_column():
    """Column: 6-char right-aligned tokens between ts and ─; None → ·; plain style is the shared baseline for both forms."""
    tl = [
        {"kind": "session_start", "ts": None, "detail": "cc 2.1.207", "ctx_tokens": None},
        {"kind": "action", "ts": None, "tool": "Bash", "detail": "ls", "ctx_tokens": 21_000},
        {"kind": "file_read", "ts": None, "detail": "Read a.md", "confidence": "exact",
         "ctx_tokens": 1_234_000},
    ]
    lines = render_timeline_lines(tl)
    assert lines[0].startswith("  1 --:--:--      · ─ session start")
    assert lines[1].startswith("  2 --:--:--    21k ─ [bash]")
    assert lines[2].startswith("  3 --:--:--   1.2M ─ [read]")
