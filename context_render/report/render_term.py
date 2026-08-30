"""Terminal renderer (A4; pure-function consumer of the intermediate aggregate object).

Width-adaptive, NO_COLOR compatible; the md renderer reuses this module's line
builders, guaranteeing the two forms stay consistent (AC3/AC9). Styling is
opt-in via ansi.Style: with the default (disabled) style every line is
byte-identical to the plain form the md renderer embeds.
"""

from __future__ import annotations

from ..config import Config
from .ansi import Style
from .charts import _short, daily_histogram, display_width, pad_to, truncate_display
from .context_map import context_map_lines
from .timeline import render_timeline_lines

STATE_LABEL = {"invoked": "invoked", "loaded": "loaded", "registered": "—"}

# Fixed layout width for the compact "not loaded" grid: independent of the live
# terminal so terminal and md forms emit identical lines (AC3/AC9).
WRAP_WIDTH = 96


def _fmt_money(v: float | None, billing: str) -> str:
    if v is None or billing != "api":
        return ""
    return f", ${v:.2f}"


def _compact_group(label: str, ids: list[str], style: Style,
                   col_w: int | None = None) -> list[str]:
    """Header + ls-style column grid: one dim block instead of one row per unused component.

    col_w lets adjacent groups share one grid so their columns line up.
    """
    lines = [style.dim(f"  {label}")]
    if col_w is None:
        col_w = max(display_width(i) for i in ids) + 3
    ncols = max(1, (WRAP_WIDTH - 4) // col_w)
    for r in range(0, len(ids), ncols):
        row = ids[r:r + ncols]
        cells = "".join(pad_to(s, col_w) for s in row[:-1]) + row[-1]
        lines.append(style.dim(f"    {cells}"))
    return lines


def _range_str(ranges: list, full: bool) -> str:
    """Per-load line ranges (:start–end). A read without offset/limit only shows as :all
    when listed alongside real ranges — alone it stays unmarked, since Read caps at
    2000 lines and "all" would overstate what entered context."""
    if all(r is None for r in ranges):
        return ""
    toks = [":all" if r is None
            else f":{r[0]}–{r[1]}" if r[1] is not None
            else f":{r[0]}–"
            for r in ranges]
    if not full and len(toks) > 4:
        toks = toks[:4] + [f"+{len(toks) - 4}"]
    return " " + " ".join(toks)


def _fmt_occ(v: int | None) -> str:
    return _short(v) if v is not None else "·"  # blank column: not computable, never guessed


def _selfderive_row(no: int, r: dict, style: Style, with_sessions: bool) -> str:
    text = r["label"] if r["kind"] == "action" else f"'{r['label']}'"
    label = pad_to(truncate_display(text, 24), 25)
    method = pad_to(truncate_display(r["method"], 30), 31)
    cells = f"{style.dim(f'{no:>4}')}  {label}{style.dim(method)}"
    if with_sessions:
        cells += f"{r['sessions']:>8}"
    return (f"{cells}{r['times']:>7}{_short(r['tokens']):>8}"
            f"{_fmt_occ(r['occupancy']):>8}")


def _selfderive_header(style: Style, with_sessions: bool) -> str:
    head = f"{'#':>4}  {pad_to('what the agent was after', 25)}{pad_to('how', 31)}"
    if with_sessions:
        head += f"{'sessions':>8}"
    return style.dim(head + f"{'times':>7}{'tokens':>8}{'window~':>8}")


def _selfderive_thesis(tokens: int, pct: float | None) -> list[str]:
    pct_s = f" ({pct}% of tool output)" if pct is not None else ""
    return [
        f"  agent spent ~{_short(tokens)} tokens{pct_s} answering",
        "  questions the harness didn't answer — each row: it searched,",
        "  then read the hits, and the answers sat in its context window",
    ]


def _story_line(r: dict) -> str | None:
    """One dim example line under a top-5 row; None when the row has no story."""
    searches = (r.get("story") or {}).get("searches") or []
    if not searches:
        return None
    tool, raw, n = searches[0]
    tool = tool.lower()  # story lines read as prose ("grep 'x'"), unlike the how column
    probe = f"{tool} '{raw}'" if raw else tool
    if n >= 2:
        probe += f" ×{n}"
    reads = (r.get("story") or {}).get("reads") or []
    if reads:
        k = len(reads)
        m = sum(c for _, c in reads)
        probe += f" → read {k} file" + ("s" if k != 1 else "")
        if m > k:
            probe += f" ×{m}"
        rep = ", ".join(f"{name} ×{c}" for name, c in reads if c >= 2)
        if rep:
            probe += f" ({rep})"
    return truncate_display(f"       ↳ {probe}", WRAP_WIDTH)


def _selfderive_table(shown: list[dict], style: Style, with_sessions: bool) -> list[str]:
    """header + rows + top-5 story lines + legend — the shared table body."""
    lines = [" " + _selfderive_header(style, with_sessions=with_sessions)]
    for no, r in enumerate(shown, 1):
        lines.append(" " + _selfderive_row(no, r, style, with_sessions=with_sessions))
        if no <= 5:
            sl = _story_line(r)
            if sl:
                lines.append(style.dim(sl))
    if any(r["heuristic"] for r in shown):
        lines.append("")
        lines.append(style.dim("    ~ = includes heuristic attribution"))
    return lines


def _stale_outcome(close_idx: int | None, outcome: str) -> str:
    if outcome == "re-read":
        return f"✓ re-read #{close_idx}"
    if outcome == "compacted":
        return "▣ compacted"
    return "✗ never re-read"


def _stale_row_line(d: dict, style: Style) -> str:
    mark = "~ " if d["confidence"] == "heuristic" else "  "
    agent = f"  [{d['agent']}]" if d.get("agent") else ""
    tail = _stale_outcome(d["close_idx"], d["outcome"])
    if d["outcome"] == "never-re-read":
        tail = style.yellow(tail)
    return (f"  {mark}{pad_to(truncate_display(d['path'], 38), 39)}"
            f"read #{d['read_idx']} → mutated #{d['mutate_idx']} ({d['mutate_tool']})"
            f" → {tail}{style.dim(agent)}")


def session_stale_lines(agg: dict, style: Style) -> list[str]:
    """STALE COPIES block (design §4): copies of repo files still sitting in the
    window after the file changed underneath them. A stale window is an accident
    site, not an accident — no verdicts. Absent entirely when the session has
    none (byte-compatibility with pre-0.6 output, AC3)."""
    windows = agg.get("stale_windows") or []
    if not windows:
        return []
    s = agg.get("stale_summary") or {}
    lines = ["", "  " + style.bold(f"STALE COPIES — {len(windows)} window(s)"), ""]
    groups: dict[tuple[int, str, str], list[dict]] = {}
    for d in windows:
        groups.setdefault((d["mutate_idx"], d["mutate_tool"], d["outcome"]), []).append(d)
    for (midx, tool, outcome), ds in sorted(groups.items()):
        if len(ds) == 1:
            lines.append(_stale_row_line(ds[0], style))
            continue
        # one wildcard mutation event hit several in-context copies → fold (§2.4)
        shown = ", ".join(d["path"] for d in ds[:3])
        if len(ds) > 3:
            shown += f" +{len(ds) - 3}"
        mark = "~ " if any(d["confidence"] == "heuristic" for d in ds) else "  "
        tail = "✓ re-read (varies)" if outcome == "re-read" else _stale_outcome(None, outcome)
        if outcome == "never-re-read":
            tail = style.yellow(tail)
        lines.append(f"  {mark}{len(ds)} files · mutated #{midx} ({tool}) → {tail}"
                     f"  {style.dim(shown)}")
    lines.append("")
    lines.append(style.dim(
        f"  {s.get('total', len(windows))} stale windows, {s.get('never', 0)} never "
        f"re-read (~{_short(s.get('tokens_never', 0))} tokens of overturned content)"))
    return lines


def session_selfderive_lines(agg: dict, full: bool, style: Style) -> list[str]:
    """SELF-DERIVATION block (§3.2): fixed ≤5 rows + header by default, --full/--md complete."""
    sd = agg.get("self_derivation")
    if sd is None:
        return ["", style.dim(
            "  self-derivation: unavailable (transcript expired before facts extraction)")]
    if not sd:
        return []
    shown = sd if full or len(sd) <= 5 else sd[:5]
    title = (f"SELF-DERIVATION — top {len(shown)} of {len(sd)} (--full for all)"
             if len(shown) < len(sd) else f"SELF-DERIVATION — {len(sd)} item(s)")
    summary = agg.get("self_derivation_summary") or {}
    tokens = summary.get("tokens", sum(r["tokens"] for r in sd))
    lines = ["", "  " + style.bold(title), ""]
    lines.extend(_selfderive_thesis(tokens, summary.get("pct")))
    lines.append("")
    lines.extend(_selfderive_table(shown, style, with_sessions=False))
    return lines


def session_lines(agg: dict, config: Config, full: bool,
                  style: Style | None = None) -> list[str]:
    """Single-session report lines (A4.2 layout). full=md full output; otherwise terminal-truncated."""
    style = style or Style()
    s = agg["session"]
    lines: list[str] = []
    started = s.get("started_at") or ""
    if started:
        try:
            from datetime import datetime

            started = datetime.fromisoformat(started).astimezone().strftime("%Y-%m-%d %H:%M")
        except ValueError:
            started = started[:16].replace("T", " ")
    head = (
        f"Session {started} (proj: {s['project']}, {s['turns']} turns"
        f"{_fmt_money(s.get('cost_usd'), agg['billing'])})"
    )
    if s["parse_status"] == "degraded":
        head += " ⚠"
    lines.append(style.bold(head))
    if agg.get("task_summary"):
        lines.append(f"{style.dim('Task summary:')} {agg['task_summary']}")
    lines.append("")

    def _used(r: dict) -> bool:
        return r["miss"] or r["state"] in ("invoked", "loaded")

    used_rows = [r for r in agg["components"] if _used(r)]
    unused_rows = [r for r in agg["components"] if not _used(r)]
    for r in used_rows:
        if r["miss"]:
            paint = style.red
            state = "MISS ⚠"
            detail = "registered, commit in session, but not triggered (heuristic)"
        elif r["state"] == "invoked":
            paint = style.green
            state = "invoked"
            detail = f"{r['invoked']} invocation(s)"
        else:
            paint = style.cyan
            state = "loaded"
            detail = f"expanded/loaded {r['loaded']} time(s)"
        mark = " ~" if r["confidence"] == "heuristic" else ""
        lines.append(f"  {paint(pad_to(state + mark, 10))}{pad_to(r['id'], 34)}{detail}")
    if used_rows and unused_rows:
        lines.append("")
    silent_hooks = [r["id"] for r in unused_rows if r["type"] == "hook"]
    silent_rest = [r["id"] for r in unused_rows if r["type"] != "hook"]
    if unused_rows:
        col_w = max(display_width(r["id"]) for r in unused_rows) + 3
    if silent_hooks:
        n = len(silent_hooks)
        lines.extend(_compact_group(
            f"not triggered ({n} hook{'s' if n > 1 else ''})", silent_hooks, style, col_w))
    if silent_rest:
        lines.extend(_compact_group(
            f"not loaded ({len(silent_rest)}, may be irrelevant to this task)",
            silent_rest, style, col_w))

    file_loads = agg.get("file_loads") or []
    if file_loads:
        lines.append("")
        lines.append("  " + style.bold(
            f"File loads (context injection order, {len(file_loads)} files)"))
        max_rows = None if full else 25
        shown = file_loads if max_rows is None else file_loads[:max_rows]
        for f in shown:
            tools = " + ".join(
                f"{t}×{n}" if n > 1 else t for t, n in sorted(f["tools"].items())
            )
            mark = " ~" if f["confidence"] == "heuristic" else ""
            rng = _range_str(f.get("ranges") or [], full)
            comp = f"  → {f['component']}" if f.get("component") else ""
            num = style.dim(f"{f['order']:>3}.")
            lines.append(
                f"  {num} {pad_to(f['path'], 44)}  {style.dim(tools + mark + rng)}{style.cyan(comp)}"
            )
        if max_rows is not None and len(file_loads) > max_rows:
            lines.append(style.dim(
                f"   … truncated {len(file_loads) - max_rows} more (use --full or --md for all)"))

    if config.graph and agg.get("timeline"):
        cm = context_map_lines(agg["timeline"], agg.get("context_samples") or [], style,
                               window_tokens=config.context_window_tokens)
        if cm:
            lines.append("")
            lines.extend(cm)
            ss = agg.get("stale_summary") or {}
            if ss.get("never"):
                lines.append(style.dim(
                    f"{'':10} ✗ {ss['never']} stale cop"
                    f"{'y' if ss['never'] == 1 else 'ies'} never re-read "
                    f"(~{_short(ss['tokens_never'])} tok overturned) — see STALE COPIES"))

    if config.timeline and agg.get("timeline"):
        lines.append("")
        lines.append("  " + style.bold("timeline"))
        max_lines = None if full else config.timeline_term_max
        for tl in render_timeline_lines(agg["timeline"], max_lines, style):
            lines.append(f"  {tl}")

    sc = agg["static_context"]
    lines.append("")
    total_tok = style.bold(f"{sc['total_tokens']:,}")
    lines.append(f"  static context injected this run: {total_tok} tokens (estimated)")
    if sc["breakdown"]:
        detail = " + ".join(f"{b['id']} {b['tokens']:,}" for b in sc["breakdown"][:4])
        lines.append(style.dim(f"  ({detail})"))

    # stale copies: overturned answers still sitting in the window (gauge, not grader)
    lines.extend(session_stale_lines(agg, style))

    # self-derivation closes the report (§3.2): what the agent went hunting for itself
    lines.extend(session_selfderive_lines(agg, full, style))

    if full:
        ev_rows = [r for r in agg["components"] if r.get("evidence")]
        if ev_rows:
            lines.append("")
            lines.append("  evidence")
            for r in ev_rows:
                lines.append(f"  {r['id']}:")
                for e in r["evidence"][:20]:
                    lines.append(f"    #{e['event_idx']} {e.get('ts') or ''} {e['summary']}")

    for w in agg.get("warnings", []):
        lines.append(style.yellow(f"  {w}"))
    for h in agg.get("hints", []):
        lines.append(style.dim(f"  {h}"))
    return lines


def _status_paint(status: str, style: Style):
    if status.startswith("☠"):
        return style.red
    if "MISS" in status or status.startswith("low-use"):
        return style.yellow
    if status.startswith("active"):
        return style.green
    return lambda x: x


def window_lines(agg: dict, config: Config, full: bool,
                 style: Style | None = None) -> list[str]:
    """Cross-session aggregate report lines (A4.3 layout)."""
    style = style or Style()
    w = agg["window"]
    lines: list[str] = []
    lines.append(style.bold(
        f"Observation window: {w['label']}, {w['session_count']} sessions (proj: {w['project']})"
    ))
    lines.append("")
    header = f"{pad_to('component', 34)}{pad_to('calls', 6)}{pad_to('last used', 12)}status"
    lines.append(style.dim(header))
    for r in agg["components"]:
        cnt = r["primary_count"]
        last = (r["last_used"] or "—")[:10]
        # Relativize last-used
        mark = "~" if r["confidence"] == "heuristic" and cnt else ""
        row_paint = style.dim if r.get("unused") else (lambda x: x)
        lines.append(
            f"{row_paint(pad_to(r['id'], 34))}{pad_to(str(cnt) + mark, 6)}"
            f"{style.dim(pad_to(last, 12))}{_status_paint(r['status'], style)(r['status'])}"
        )

    if config.graph and agg.get("daily"):
        daily = agg["daily"]
        counts = [c for _, c in daily]
        summary = f"  {len(daily)} days · {sum(counts)} sessions · peak {max(counts)}/day"
        lines.append("")
        lines.append(style.bold("Daily activity (sessions/day)") + style.dim(summary))
        lines.extend(daily_histogram(daily, bar_paint=style.cyan))

    # zero sessions → nothing to summarize; the warning below carries the story
    if w["session_count"]:
        st = agg["static"]
        lines.append("")
        lines.append(style.bold(
            f"Static context {st['total_tokens']:,} tokens per session (estimated)"))
        if agg["billing"] == "api" and st.get("cost_usd") is not None:
            lines.append(
                f"Estimated measured static-context spend in window ${st['cost_usd']:.2f} (approx.)"
            )
            if agg["cost"].get("total_usd") is not None:
                lines.append(f"Total measured session spend in window ${agg['cost']['total_usd']:.2f} (from usage)")
        else:
            lines.append(style.dim("(subscription / billing unspecified: showing tokens only)"))

    for warning in agg.get("warnings", []):
        lines.append("")
        lines.append(style.yellow(warning))
    if agg.get("hints"):
        lines.append("")
        for h in agg["hints"]:
            lines.append(style.dim(f"Hint: {h}" if not h.startswith("Hint") else h))
    return lines


ANALYZE_TERM_MAX = 20  # terminal truncation (--md is always complete, same as other reports)


def analyze_lines(agg: dict, config: Config, full: bool,
                  style: Style | None = None) -> list[str]:
    """`analyze` output (§3.1): one summary line + one table, no other sections."""
    style = style or Style()
    w = agg["window"]
    s = agg["summary"]
    lines = [style.bold(
        f"Self-derivation cost — {w['label']}, {w['session_count']} sessions "
        f"(facts: {w['facts_sessions']} of {w['session_count']})"
    )]
    rows = agg["rows"]
    if rows:
        lines.append("")
        lines.extend(_selfderive_thesis(s["tokens"], s["pct"]))
        lines.append("")
        shown = rows if full else rows[:ANALYZE_TERM_MAX]
        lines.extend(_selfderive_table(shown, style, with_sessions=True))
        if len(shown) < len(rows):
            lines.append(style.dim(
                f"   … truncated {len(rows) - len(shown)} more (use --md for all)"))
    elif not agg.get("warnings"):
        lines.append("")
        lines.append(style.dim("  (no self-derivation detected in window)"))
    for warning in agg.get("warnings", []):
        lines.append("")
        lines.append(style.yellow(warning))
    return lines


COVERAGE_TERM_MAX = 15


def coverage_lines(agg: dict, config: Config, full: bool,
                   style: Style | None = None) -> list[str]:
    """`coverage` output (guidance-reachability spec §5): counts + hop distribution +
    cost-sorted unreachable + wording-failure candidates + stale refs. Coverage numbers
    are facts, never scores — the necessary-not-sufficient line is part of the report."""
    style = style or Style()
    f, p, s = agg["files"], agg["py"], agg["symbols"]
    lines = [style.bold(
        f"Guidance reachability — {f['reachable']}/{f['total']} files from root CLAUDE.md")]
    if not agg["root_present"]:
        lines += ["", style.yellow(
            "⚠ no root CLAUDE.md — the map has no entry point (a signal, not an error)")]
    lines.append("")
    sym = f"symbols: {s['reachable']}/{s['total']}"
    if agg["parse_failed"]:
        sym += f" ({agg['parse_failed']} unparsed .py excluded)"
    lines.append(f"  py: {p['reachable']}/{p['total']}   {sym}")
    if agg["hop_dist"]:
        hops = "  ".join(f"{h} hops ×{n}" for h, n in agg["hop_dist"].items())
        lines.append(f"  hop depth (.py): {hops}")
    lines.append(style.dim(
        "  reachable is necessary, not sufficient — whether the wording actually guides "
        "the agent is the acquisition trace's question"))

    if agg["unreachable"]:
        lines += ["", style.bold(
            f"  unreachable .py — sorted by observed self-derivation cost "
            f"({agg['window_label']}):")]
        shown = agg["unreachable"] if full else agg["unreachable"][:COVERAGE_TERM_MAX]
        for r in shown:
            defs = "·" if r["defs"] is None else str(r["defs"])
            cost = (f"   grepped {r['grep_count']}×, ~{_short(r['tokens_est'])} tokens"
                    if r["grep_count"] else "")
            lines.append(f"    {pad_to(truncate_display(r['path'], 44), 45)}"
                         f"{defs:>4} defs{cost}")
        if len(shown) < len(agg["unreachable"]):
            lines.append(style.dim(
                f"    … truncated {len(agg['unreachable']) - len(shown)} more "
                "(use --md for all)"))
    if agg["grepped_but_reachable"]:
        lines += ["", style.bold(
            "  reachable but still grepped (wording-failure candidates) ~:")]
        for r in agg["grepped_but_reachable"]:
            lines.append(f"    {pad_to(truncate_display(r['path'], 44), 45)}"
                         f"hop {r['hop']}   grepped {r['grep_count']}×, "
                         f"~{_short(r['tokens_est'])} tokens")
    if agg["stale"]:
        lines += ["", style.bold("  stale references:")]
        for r in agg["stale"]:
            lines.append(f"    {r['carrier']} → '{r['raw']}'")
    if not agg["joined"]:
        lines += ["", style.dim(
            "  (no observed searches to join — run `ctxr sync` first)")]
    return lines


def render_term(agg: dict, config: Config, full: bool = False) -> str:
    style = Style.detect()
    if agg["report_type"] == "last":
        return "\n".join(session_lines(agg, config, full=full, style=style))
    if agg["report_type"] == "analyze":
        return "\n".join(analyze_lines(agg, config, full=full, style=style))
    if agg["report_type"] == "coverage":
        return "\n".join(coverage_lines(agg, config, full=full, style=style))
    return "\n".join(window_lines(agg, config, full=full, style=style))
