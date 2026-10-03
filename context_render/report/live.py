"""Live snapshot for the ctxr-live Claude Code mod: aggregate → JSON-ready dict.

Pure over the aggregate object (renderers never touch store/filesystem). The map ships
as segments (context_map_parts) so the band draws the same geometry as the reports;
timeline rows keep every aggregate field plus their listing number `no`.
"""

from __future__ import annotations

from .context_map import context_map_parts, window_scale

LIVE_SCHEMA_VERSION = 2
# `ctxr live` without a manifest scans in memory; nothing archives the session until init
NO_MANIFEST_NOTICE = "not init'd: scaffolds scanned on the fly, session not archived — run ctxr init"


def live_snapshot(agg: dict, width: int, window_tokens: int,
                  notice: str | None = None) -> dict:
    timeline = agg.get("timeline") or []
    samples = agg.get("context_samples") or []
    rows, axis = context_map_parts(timeline, samples, width=width,
                                   window_tokens=window_tokens)
    latest = max(samples, key=lambda s: s["idx"]) if samples else None
    session = agg.get("session") or {}
    return {
        "schema_version": LIVE_SCHEMA_VERSION,
        "session_id": session.get("id"),
        "cc_version": session.get("cc_version"),
        "width": width,
        "map": rows,
        "axis": axis,
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
