"""Timeline assembly (§5.2): full in-session event timeline, the first-class view of agentic coding."""

from __future__ import annotations

from ..attributor.rules import TimelineEntry
from .ansi import Style
from .charts import _short, pad_to, truncate_display

# action-tag colors: what the agent read is blue, what it changed is yellow,
# what it ran is magenta; anything else stays neutral cyan
_ACTION_COLOR = {"read": "blue", "edit": "yellow", "write": "yellow", "bash": "magenta"}


def timeline_dicts(entries: list[TimelineEntry], ts_reliable: bool) -> list[dict]:
    """Timeline array of the intermediate aggregate object (A4.1)."""
    out = []
    for e in entries:
        d = {
            "ts": e.ts.isoformat() if e.ts else None,
            "component": e.component_id,
            "kind": e.kind if e.kind != "transition" else (e.component_type or "component"),
            "transition": e.state,
            "detail": e.detail,
            "confidence": e.confidence,
            "evidence_ref": e.idx,
            "est_tokens": e.est_tokens,
            "order": "ts" if ts_reliable else "idx",
        }
        if e.is_sidechain:
            d["sidechain"] = True
        if e.kind == "action":
            d["kind"] = "action"
            d["tool"] = e.component_type
        out.append(d)
    return out


def _fmt_ts(ts_iso: str | None) -> str:
    if not ts_iso:
        return "--:--:--"
    # ISO → local-readable HH:MM:SS
    try:
        from datetime import datetime

        return datetime.fromisoformat(ts_iso).astimezone().strftime("%H:%M:%S")
    except ValueError:
        return "--:--:--"


def _paint_detail(detail: str, style: Style) -> str:
    if detail.endswith(" (failed)"):
        return detail[: -len(" (failed)")] + style.red(" (failed)")
    return detail


def _fmt_ctx(ctx: int | None, style: Style) -> str:
    """Measured window occupancy column; None (no sample yet / post-compaction /
    sidechain window) renders as a dim placeholder dot."""
    if ctx is None:
        return style.dim(f"{'·':>6}")
    return f"{_short(ctx):>6}"


def render_timeline_lines(timeline: list[dict], max_lines: int | None = None,
                          style: Style | None = None) -> list[str]:
    """Timeline character layout (shared by both forms, guaranteeing consistency AC9).

      1 14:02:31      · ─ session start (cc 2.1.0)
      2 14:03:05    21k ─ [I] skill: pdf          expanded
      3 14:12:10      · ─ ⟐  compaction

    Each row is numbered by its position in the full timeline; the context window
    map labels its ▼/▲ marks with these numbers, so both views cross-reference.
    Colors ride on top of the plain layout: with a disabled/absent style the
    output is byte-identical to the plain form (md path).
    """
    style = style or Style()
    lines: list[str] = []
    truncated = 0
    entries = timeline
    if max_lines is not None and len(entries) > max_lines:
        truncated = len(entries) - max_lines
        entries = entries[:max_lines]
    for no, e in enumerate(entries, 1):
        pre = (f"{style.dim(f'{no:>3}')} {style.dim(_fmt_ts(e.get('ts')))} "
               f"{_fmt_ctx(e.get('ctx_tokens'), style)} {style.dim('─')} ")
        kind = e.get("kind")
        if kind == "session_start":
            lines.append(f"{pre}{style.bold('session start')} ({e.get('detail', '')})")
        elif kind == "session_end":
            lines.append(f"{pre}{style.bold('session end')}{'':26}{e.get('detail', '')}")
        elif kind == "compaction":
            lines.append(f"{pre}{style.magenta('⟐  compaction')}")
        elif kind == "file_read":
            mark = style.dim(" ~") if e.get("confidence") == "heuristic" else ""
            lines.append(f"{pre}{style.blue(pad_to('[read]', 8))}{e.get('detail', '')}{mark}")
        elif kind == "action":
            tool = (e.get("tool") or "act").lower()
            paint = getattr(style, _ACTION_COLOR.get(tool, "cyan"))
            detail = _paint_detail(truncate_display(e.get("detail", ""), 78), style)
            lines.append(f"{pre}{paint(pad_to(f'[{tool}]', 8))}{detail}")
        else:
            state = e.get("transition")
            tag = {"loaded": "[L]", "invoked": "[I]"}.get(state or "", "[?]")
            paint = style.green if state == "invoked" else style.cyan
            comp = e.get("component") or "?"
            mark = " ~" if e.get("confidence") == "heuristic" else ""
            label = paint(pad_to(f"{tag} {comp}{mark}", 44))
            lines.append(f"{pre}{label}  {e.get('detail', '')}")
    if truncated:
        lines.append(style.dim(f"… truncated {truncated} more (use --full or --md for all)"))
    if timeline and timeline[0].get("order") == "idx":
        lines.append(style.dim("(ts missing/out-of-order: ordered by event index, order inferred)"))
    return lines
