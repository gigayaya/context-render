"""Intermediate aggregate object (A4.1, internal; JSON not exposed to users).

The terminal/markdown renderers are pure-function renderers of this object,
guaranteeing the two forms stay consistent (AC3/AC9). MUST include
schema_version (M1 = 1). In M2, if machine-readable output is needed, expose
this object directly as JSON.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..attributor.rules import Attribution
from ..config import Config
from ..cost import compute_session_cost, deadweight_cost
from ..inventory.scanner import Component
from ..parser.loader import ParsedSession
from ..parser.versions import is_supported
from ..pipeline import COST_CID, GIT_COMMIT_CID
from ..store import Store
from .timeline import timeline_dicts

SCHEMA_VERSION = 1

# Built-in hint text (A4.3, MUST)
HINT_LOW_USE = (
    "Hint: low-use components may stem from the task mix in this window; "
    "before deleting, consider widening the observation window (--since 90d)."
)
HINT_CLAUDE_MD = (
    "CLAUDE.md (root) is always injected and its rule-level usage is unmeasurable (see README); "
    "task-specific knowledge in it is better moved into a skill — once moved out it becomes "
    "measurable, on-demand dynamic context."
)
HINT_EXPAND_WINDOW = (
    "Insufficient observation sample (window <90d or sessions <20): deadweight verdicts have "
    "limited confidence; consider widening the observation window before deciding."
)

STATE_ORDER = {"invoked": 3, "loaded": 2, "registered": 1}


def _attach_ctx_tokens(timeline: list[dict], samples: list[dict]) -> None:
    """Measured window occupancy per timeline row (carry-forward by event idx).

    A row shows the latest main-thread sample at or before its event (a sample
    at the same idx is the assistant message that issued the action, so it
    counts); a compaction row clears the carry until the next sample — the
    pre-compaction number is known-wrong after the rewrite; sidechain rows stay
    None: their events live in the subagent's own window, mirroring the
    context_samples exclusion.
    """
    sweep = sorted(
        [(s["idx"], 0, i, s) for i, s in enumerate(samples)]
        + [(t["evidence_ref"], 1, i, t) for i, t in enumerate(timeline)],
        key=lambda x: x[:3],
    )
    carry: int | None = None
    for _, is_row, _, obj in sweep:
        if not is_row:
            carry = obj["tokens"]
        elif obj["kind"] == "compaction":
            carry = None
            obj["ctx_tokens"] = None
        else:
            obj["ctx_tokens"] = None if obj.get("sidechain") else carry


def _display_tokens(c: Component) -> int:
    return c.dead_weight_tokens


def _static_breakdown(components: list[Component]) -> tuple[int, list[dict]]:
    items = []
    total = 0
    for c in components:
        if c.missing:
            continue
        t = c.static_tokens
        if t > 0:
            items.append({"id": c.id, "tokens": t})
            total += t
    items.sort(key=lambda d: -d["tokens"])
    return total, items


def aggregate_session(parsed: ParsedSession, att: Attribution,
                      components: list[Component], config: Config,
                      include_evidence: bool = False) -> dict:
    """Single-session aggregate (last, A4.2)."""
    comps = [c for c in components if not c.missing]
    static_total, breakdown = _static_breakdown(comps)
    cost = compute_session_cost(parsed.events, static_total, config)
    # same contract as the DB write path (pipeline.build_rows, A11): version not in the
    # supported list → every attribution shown as heuristic, or `last` and `report`
    # would disagree about the very same session
    version_ok = is_supported(parsed.cc_version)

    rows = []
    for c in comps:
        counts = {}
        confidence = "exact" if version_ok else "heuristic"
        evidence: list[dict] = []
        for state in ("invoked", "loaded", "registered"):
            agg = att.usages.get((c.id, state))
            if agg:
                counts[state] = agg.count
                if agg.confidence == "heuristic" and state != "registered":
                    confidence = "heuristic"
                if include_evidence and state != "registered":
                    evidence.extend(agg.evidence)
        best = max((s for s in counts if counts[s] > 0), key=lambda s: STATE_ORDER[s],
                   default=None)
        miss = (
            c.type == "hook"
            and c.miss_when == "git_commit"
            and att.git_commit_count > 0
            and counts.get("invoked", 0) == 0
        )
        rows.append(
            {
                "id": c.id,
                "type": c.type,
                "state": best or "registered",
                "invoked": counts.get("invoked", 0),
                "loaded": counts.get("loaded", 0),
                "confidence": confidence,
                "miss": miss,
                "max_state": ("invoked" if "invoked" in c.states
                              else "loaded" if "loaded" in c.states else "registered"),
                "evidence": evidence if include_evidence else [],
            }
        )
    rows.sort(key=lambda r: (-STATE_ORDER.get(r["state"], 0), r["id"]))

    warnings = []
    if parsed.cc_version and not version_ok:
        warnings.append(
            f"⚠ version {parsed.cc_version} not in the support matrix: "
            "best-effort parse, all attributions marked heuristic"
        )
    if parsed.parse_status == "degraded":
        counts_s = ", ".join(f"{k}×{v}" for k, v in parsed.unknown_type_counts.items())
        warnings.append(f"⚠ parse degraded: unknown events [{counts_s}]" +
                        (f", bad lines {parsed.bad_line_count}" if parsed.bad_line_count else ""))
    if parsed.ts_out_of_order:
        warnings.append("⚠ timestamps out of order: timeline ordered by event index (order inferred)")
    if not cost.has_usage:
        warnings.append("⚠ no usage field: cost degraded to unavailable")
    if cost.unknown_models:
        warnings.append(
            f"⚠ unknown model prices ({', '.join(cost.unknown_models)}): "
            "cost excludes these messages; override in config.yaml prices"
        )

    # File loads (context injection order): aggregate by path, keeping first-seen order and per-tool counts.
    # Sidechain reads land in the subagent's own window, not this one → excluded here
    # (they still mark components and show in the timeline, tagged).
    file_loads: list[dict] = []
    by_path: dict[str, dict] = {}
    for fr in att.file_reads:
        if fr.is_sidechain:
            continue
        entry = by_path.get(fr.path)
        if entry is None:
            entry = {
                "order": len(file_loads) + 1,
                "path": fr.path,
                "count": 0,
                "tools": {},
                "confidence": "exact" if version_ok else "heuristic",
                "component": fr.component_id,
                "first_ts": fr.ts.isoformat() if fr.ts else None,
                "ranges": [],
            }
            by_path[fr.path] = entry
            file_loads.append(entry)
        entry["count"] += 1
        entry["tools"][fr.tool] = entry["tools"].get(fr.tool, 0) + 1
        if fr.confidence == "heuristic":
            entry["confidence"] = "heuristic"
        if fr.tool == "Read":  # only Read carries offset/limit; Bash ranges would be heuristic
            entry["ranges"].append(list(fr.line_range) if fr.line_range else None)

    # Context-window samples: prompt-side usage per main-thread assistant message
    # ≈ context occupancy at that turn (sidechains have their own window, excluded)
    context_samples: list[dict] = []
    for ev in parsed.events:
        if ev.kind != "assistant_msg" or ev.usage is None or ev.is_sidechain:
            continue
        occupied = (ev.usage.input_tokens + ev.usage.cache_read_input_tokens
                    + ev.usage.cache_creation_input_tokens)
        if occupied > 0:
            context_samples.append(
                {"idx": ev.idx, "ts": ev.ts.isoformat() if ev.ts else None,
                 "tokens": occupied}
            )

    timeline = timeline_dicts(att.timeline, ts_reliable=not parsed.ts_out_of_order)
    _attach_ctx_tokens(timeline, context_samples)

    return {
        "schema_version": SCHEMA_VERSION,
        "report_type": "last",
        "session": {
            "id": parsed.session_id,
            "project": Path(parsed.cwd).name if parsed.cwd else "?",
            "started_at": att.started_at.isoformat() if att.started_at else None,
            "turns": att.turns,
            "cost_usd": cost.cost_usd,
            "cc_version": parsed.cc_version,
            "parse_status": parsed.parse_status,
            "total_tokens": att.total_tokens,
            "compactions": att.compaction_count,
        },
        "billing": config.billing,
        "task_summary": att.summary_text or (att.prompt_digest or "")[:80],
        "components": rows,
        "file_loads": file_loads,
        "context_samples": context_samples,
        "timeline": timeline,
        "static_context": {"total_tokens": static_total, "breakdown": breakdown[:10]},
        "warnings": warnings,
        "hints": [],
    }


def _window_days(since_iso: str | None) -> float:
    if not since_iso:
        return 3650.0
    dt = datetime.fromisoformat(since_iso)
    return max(1.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)


def aggregate_window(store: Store, components: list[Component], config: Config,
                     since_iso: str | None, since_label: str,
                     deadweight_only: bool = False) -> dict:
    """Cross-session aggregate (report/deadweight, A4.3). Aggregation always recomputes from the DB."""
    comps = [c for c in components if not c.missing]
    sessions = store.sessions_since(since_iso)
    session_ids = [r["id"] for r in sessions]
    usage_rows = store.usages_for_sessions(session_ids)

    per_comp: dict[str, dict] = defaultdict(
        lambda: {"invoked": 0, "loaded": 0, "last_used": None, "confidence": "exact",
                 "sessions_invoked": set()}
    )
    git_commit_sessions: set[str] = set()
    cost_total = 0.0
    static_cost_total = 0.0
    cost_available = False
    session_start: dict[str, str | None] = {r["id"]: r["started_at"] for r in sessions}

    for row in usage_rows:
        cid = row["component_id"]
        if cid == GIT_COMMIT_CID:
            git_commit_sessions.add(row["session_id"])
            continue
        if cid == COST_CID:
            try:
                d = json.loads(row["evidence"] or "{}")
            except json.JSONDecodeError:
                d = {}
            if d.get("cost_usd") is not None:
                cost_total += d["cost_usd"]
                cost_available = True
            if d.get("static_cost_usd") is not None:
                static_cost_total += d["static_cost_usd"]
            continue
        if cid.startswith("_"):
            continue
        info = per_comp[cid]
        if row["state"] in ("invoked", "loaded"):
            info[row["state"]] += row["count"]
            if row["confidence"] == "heuristic":
                info["confidence"] = "heuristic"
            started = session_start.get(row["session_id"])
            if started and (info["last_used"] is None or started > info["last_used"]):
                info["last_used"] = started
            if row["state"] == "invoked":
                info["sessions_invoked"].add(row["session_id"])

    # hook MISS: count of sessions where the manifest miss_when event occurred but the hook has no trigger record (A2.3)
    hook_invoked_sessions: dict[str, set[str]] = defaultdict(set)
    for row in usage_rows:
        if row["state"] == "invoked" and not row["component_id"].startswith("_"):
            hook_invoked_sessions[row["component_id"]].add(row["session_id"])

    window_days = _window_days(since_iso)
    now = datetime.now(timezone.utc)
    low_max = config.low_use_max_count

    rows = []
    deadweight_tokens = 0
    deadweight_static_tokens = 0  # static-injection portion of deadweight components (for share/cost estimate, A9)
    has_sessions = bool(sessions)
    for c in comps:
        info = per_comp.get(c.id, {"invoked": 0, "loaded": 0, "last_used": None,
                                   "confidence": "exact", "sessions_invoked": set()})
        observable_l_or_i = "loaded" in c.states or "invoked" in c.states
        primary = info["invoked"] if "invoked" in c.states else info["loaded"]
        used_any = (info["invoked"] + info["loaded"]) > 0

        miss_count = 0
        if c.type == "hook" and c.miss_when == "git_commit":
            invoked_in = hook_invoked_sessions.get(c.id, set())
            miss_count = len(git_commit_sessions - invoked_in)

        # Status-verdict vocabulary (A2.3), neutral wording
        stale = False
        if info["last_used"]:
            age_days = (now - datetime.fromisoformat(info["last_used"])).total_seconds() / 86400
            stale = age_days > window_days * 2 / 3
        if not has_sessions:
            # zero observed sessions can prove nothing — a deadweight verdict here would
            # tell the user to delete a scaffold that was simply never measured
            status = "no data"
        elif c.type == "hook":
            if info["invoked"] == 0:
                status = "☠ never triggered ⚠ check config"
            elif primary <= low_max or stale:
                status = "low-use"
            else:
                status = "active"
            if miss_count and info["invoked"] > 0:
                status += f" ({miss_count} MISS ⚠, heuristic)"
        elif not used_any and observable_l_or_i:
            # root/global claude_md is always L, never falls into deadweight (L every session)
            status = f"☠ deadweight ({_display_tokens(c):,} tokens)"
            deadweight_tokens += _display_tokens(c)
            deadweight_static_tokens += c.static_tokens
        elif primary <= low_max or stale:
            status = "low-use"
        else:
            status = "active"
        if "invoked" not in c.states and used_any and not status.startswith("☠"):
            status += " (loaded)"

        rows.append(
            {
                "id": c.id,
                "type": c.type,
                "invoked": info["invoked"],
                "loaded": info["loaded"],
                "primary_count": primary,
                "last_used": info["last_used"],
                "status": status,
                "miss_count": miss_count,
                "tokens": _display_tokens(c),
                "confidence": info["confidence"],
                "dead": status.startswith("☠ deadweight")
                or status.startswith("☠ never triggered"),
            }
        )
    rows.sort(key=lambda r: (-r["primary_count"], r["id"]))

    static_total, _ = _static_breakdown(comps)
    dw_cost = deadweight_cost(
        static_cost_total if cost_available else None, deadweight_static_tokens, static_total
    )

    # Daily activity
    day_counts: dict[str, int] = defaultdict(int)
    for r in sessions:
        if r["started_at"]:
            day_counts[r["started_at"][:10]] += 1
    daily = []
    if day_counts:
        days_sorted = sorted(day_counts)
        first = datetime.fromisoformat(days_sorted[0]).date()
        last = datetime.fromisoformat(days_sorted[-1]).date()
        if (last - first).days <= 40:
            d = first
            while d <= last:
                key = d.isoformat()
                daily.append((key[5:], day_counts.get(key, 0)))
                d += timedelta(days=1)
        else:
            daily = [(k[5:], day_counts[k]) for k in days_sorted]

    hints = []
    if has_sessions:  # usage hints presume usage data; with none they are noise
        small_window = window_days < config.deadweight_min_window_days or len(sessions) < config.deadweight_min_sessions
        if deadweight_only and small_window:
            hints.append(HINT_EXPAND_WINDOW)
        if any(r["status"].startswith("low-use") for r in rows):
            hints.append(HINT_LOW_USE)
        root = next((c for c in comps if c.id == "claude-md:root"), None)
        if root:
            hints.append(f"CLAUDE.md (root) {root.tokens_est or 0:,} tokens " + HINT_CLAUDE_MD)

    warnings = []
    if not has_sessions:
        warnings.append(
            "⚠ no ingested sessions in window — usage cannot be judged; "
            "run `context-render sync` or widen --since"
        )
    if config.billing == "auto":
        warnings.append(
            "⚠ billing=auto inference is unreliable: set billing: api|subscription in config.yaml; "
            "currently showing tokens and shares only"
        )
    degraded_n = sum(1 for r in sessions if r["parse_status"] == "degraded")
    if degraded_n:
        warnings.append(f"⚠ {degraded_n} session(s) parse-degraded (details in sync stderr)")

    return {
        "schema_version": SCHEMA_VERSION,
        "report_type": "deadweight" if deadweight_only else "report",
        "window": {
            "label": since_label,
            "since": since_iso,
            "days": round(window_days, 1),
            "session_count": len(sessions),
            "project": sessions[0]["project"] if sessions else "?",
        },
        "billing": config.billing,
        "components": [r for r in rows if (not deadweight_only or r["dead"])],
        "daily": daily,
        "deadweight": {
            "tokens": deadweight_tokens,
            "static_tokens": deadweight_static_tokens,
            "pct": round(deadweight_static_tokens / static_total * 100, 1)
            if static_total else 0.0,
            "cost_usd": dw_cost,
        },
        "static": {
            "total_tokens": static_total,
            "cost_usd": round(static_cost_total, 4) if cost_available else None,
        },
        "cost": {"total_usd": round(cost_total, 4) if cost_available else None},
        "hints": hints,
        "warnings": warnings,
    }
