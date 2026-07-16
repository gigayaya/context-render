"""Terminal renderer (A4; pure-function consumer of the intermediate aggregate object).

Width-adaptive, NO_COLOR compatible; the md renderer reuses this module's line
builders, guaranteeing the two forms stay consistent (AC3/AC9). Styling is
opt-in via ansi.Style: with the default (disabled) style every line is
byte-identical to the plain form the md renderer embeds.
"""

from __future__ import annotations

from ..config import Config
from .ansi import Style
from .charts import _short, daily_histogram, display_width, hbar_chart, pad_to, truncate_display
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
    label = pad_to(truncate_display(r["label"], 18), 19)
    method = pad_to(truncate_display(r["method"], 26), 27)
    cells = f"{style.dim(f'{no:>4}')}  {label}{style.dim(method)}"
    if with_sessions:
        cells += f"{r['sessions']:>8}"
    return (f"{cells}{r['times']:>7}{_short(r['tokens']):>8}"
            f"{_fmt_occ(r['occupancy']):>8}")


def _selfderive_header(style: Style, with_sessions: bool) -> str:
    head = f"{'#':>4}  {pad_to('what the agent was after', 46)}"
    if with_sessions:
        head += f"{'sessions':>8}"
    return style.dim(head + f"{'times':>7}{'tokens':>8}{'window~':>8}")


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
    lines = ["", "  " + style.bold(title), " " + _selfderive_header(style, with_sessions=False)]
    for no, r in enumerate(shown, 1):
        lines.append(" " + _selfderive_row(no, r, style, with_sessions=False))
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
    """Cross-session aggregate report lines (A4.3 layout; deadweight is a focused subset)."""
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
        row_paint = style.dim if r["dead"] and not r["status"].startswith("☠ never") else (lambda x: x)
        lines.append(
            f"{row_paint(pad_to(r['id'], 34))}{pad_to(str(cnt) + mark, 6)}"
            f"{style.dim(pad_to(last, 12))}{_status_paint(r['status'], style)(r['status'])}"
        )

    if config.graph and agg.get("daily") and agg["report_type"] == "report":
        daily = agg["daily"]
        counts = [c for _, c in daily]
        summary = f"  {len(daily)} days · {sum(counts)} sessions · peak {max(counts)}/day"
        lines.append("")
        lines.append(style.bold("Daily activity (sessions/day)") + style.dim(summary))
        lines.extend(daily_histogram(daily, bar_paint=style.cyan))

    # zero sessions → no deadweight verdict to summarize; the warning below carries the story
    if w["session_count"]:
        dw = agg["deadweight"]
        st = agg["static"]
        lines.append("")
        lines.append(style.bold(
            f"Deadweight total {dw['tokens']:,} tokens; of which static injection {dw.get('static_tokens', 0):,} "
            f"= {dw['pct']}% of static context (estimated)"
        ))
        if agg["billing"] == "api" and st.get("cost_usd") is not None:
            line = (
                f"Estimated measured static-context spend in window ${st['cost_usd']:.2f} (approx.)"
            )
            if dw.get("cost_usd") is not None:
                line += f", of which deadweight is about ${dw['cost_usd']:.2f}"
            lines.append(line)
            if agg["cost"].get("total_usd") is not None:
                lines.append(f"Total measured session spend in window ${agg['cost']['total_usd']:.2f} (from usage)")
        else:
            lines.append(style.dim("(subscription / billing unspecified: showing tokens and shares only)"))

    if config.graph and agg["report_type"] == "deadweight" and agg["components"]:
        items = [(r["id"], float(r["tokens"])) for r in agg["components"] if r["tokens"] > 0]
        if items:
            lines.append("")
            lines.append(style.bold("Deadweight token share"))
            lines.extend(hbar_chart(items, bar_paint=style.red))

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
        pct = f" ({s['pct']}% of tool output)" if s["pct"] is not None else ""
        lines.append("")
        lines.append(f"  agent spent ~{_short(s['tokens'])} tokens{pct} acquiring")
        lines.append("  information the harness didn't provide")
        lines.append("")
        lines.append(" " + _selfderive_header(style, with_sessions=True))
        shown = rows if full else rows[:ANALYZE_TERM_MAX]
        for no, r in enumerate(shown, 1):
            lines.append(" " + _selfderive_row(no, r, style, with_sessions=True))
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


def render_term(agg: dict, config: Config, full: bool = False) -> str:
    style = Style.detect()
    if agg["report_type"] == "last":
        return "\n".join(session_lines(agg, config, full=full, style=style))
    if agg["report_type"] == "analyze":
        return "\n".join(analyze_lines(agg, config, full=full, style=style))
    return "\n".join(window_lines(agg, config, full=full, style=style))
