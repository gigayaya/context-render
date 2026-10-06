"""Live snapshot for the ctxr-live Claude Code mod: aggregate → JSON-ready dict.

Pure over the aggregate object (renderers never touch store/filesystem). The map ships
as segments (context_map_parts, scroll layout) so the band draws the reports' geometry;
timeline rows keep every aggregate field plus their listing number `no`; `last` names the
timeline row behind each arrow row's rightmost mark (the band writes its name there).
`vertical` is the sidebar form (context_map_vertical) when a height is given, else None.
"""

from __future__ import annotations

from .context_map import context_map_parts, context_map_vertical, last_marks, window_scale

LIVE_SCHEMA_VERSION = 4
# `ctxr live` without a manifest scans in memory; nothing archives the session until init
NO_MANIFEST_NOTICE = "not init'd: scaffolds scanned on the fly, session not archived — run ctxr init"


def live_snapshot(agg: dict, width: int, window_tokens: int,
                  notice: str | None = None, height: int | None = None) -> dict:
    timeline = agg.get("timeline") or []
    samples = agg.get("context_samples") or []
    # scroll: marks stay put as the session grows (the band redraws on every refresh)
    rows, axis = context_map_parts(timeline, samples, width=width,
                                   window_tokens=window_tokens, layout="scroll")
    last = last_marks(timeline, samples, width=width, layout="scroll")
    latest = max(samples, key=lambda s: s["idx"]) if samples else None
    session = agg.get("session") or {}
    return {
        "schema_version": LIVE_SCHEMA_VERSION,
        "session_id": session.get("id"),
        "cc_version": session.get("cc_version"),
        "width": width,
        "map": rows,
        "axis": axis,
        "last": last,
        # sidebar map (ctxr-live `layout: sidebar`), only when the mod passed --height
        "vertical": (context_map_vertical(timeline, samples, height)
                     if height is not None else None),
        "timeline": [{**e, "no": no, "sidechain": bool(e.get("sidechain"))}
                     for no, e in enumerate(timeline, 1)],
        "occupancy": {
            "current": latest["tokens"] if latest else None,
            "peak": max(s["tokens"] for s in samples) if samples else None,
            "window": window_scale(samples, window_tokens),
        },
        "warnings": list(agg.get("warnings") or []),
        "notice": notice,
    }
