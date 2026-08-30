"""discovery → parse → attribute → store pipeline (shared by sync and last)."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .attributor import Attribution, attribute
from .attributor.facts import FACTS_EXTRACTOR_VERSION, FactExtraction, extract_facts
from .attributor.stale import STALE_EXTRACTOR_VERSION, StaleWindow, extract_stale
from .config import Config, audit_dir
from .cost import compute_session_cost
from .errors import PreconditionError
from .inventory.scanner import Component, load_manifest
from .parser import ParsedSession, discover_sessions, parse_file
from .parser.discovery import SessionFile
from .parser.versions import is_supported
from .store import FACTS_CID, STALE_CID, Store
from .store.db import dumps_evidence, now_iso

GIT_COMMIT_CID = "_event:git_commit"
COST_CID = "_cost:static"


@dataclass
class ScanSummary:
    new: int = 0
    updated: int = 0
    skipped: int = 0
    degraded: int = 0
    failed: int = 0

    def line(self) -> str:
        base = (f"{self.new} new, {self.updated} updated, "
                f"{self.skipped} skipped, {self.degraded} degraded")
        return base + (f", {self.failed} failed" if self.failed else "")


def parse_since(spec: str | None) -> datetime | None:
    """--since: accepts 30d / 12w / 2026-06-01; bare dates/naive timestamps are local
    wall-clock (matching the --since help text), explicit offsets are honored. Returns UTC."""
    if not spec:
        return None
    spec = spec.strip()
    now = datetime.now(timezone.utc)
    if spec.endswith("d") and spec[:-1].isdigit():
        return now - timedelta(days=int(spec[:-1]))
    if spec.endswith("w") and spec[:-1].isdigit():
        return now - timedelta(weeks=int(spec[:-1]))
    try:
        dt = datetime.fromisoformat(spec)
    except ValueError:
        raise PreconditionError(f"--since format not supported: {spec} (accepts 30d / 12w / 2026-06-01)")
    if dt.tzinfo is None:
        dt = dt.astimezone()  # attach the local timezone
    return dt.astimezone(timezone.utc)


def open_store(repo_root: Path) -> Store:
    return Store(audit_dir(repo_root) / "db.sqlite")


def build_fact_rows(session_id: str, ext: FactExtraction) -> list[dict]:
    return [
        {
            "session_id": session_id,
            "idx": f.idx,
            "kind": f.kind,
            "key": f.key,
            "raw": f.raw,
            "tool": f.tool,
            "tokens_est": f.tokens_est,
            "occupancy_est": f.occupancy_est,
            "sidechain": int(f.sidechain),
            "confidence": f.confidence,
        }
        for f in ext.facts
    ]


def build_stale_rows(session_id: str, windows: list[StaleWindow]) -> list[dict]:
    return [
        {
            "session_id": session_id,
            "window_side": int(w.window_side),
            "window_agent": w.window_agent,
            "path": w.path,
            "read_idx": w.read_idx,
            "mutate_idx": w.mutate_idx,
            "mutate_tool": w.mutate_tool,
            "close_idx": w.close_idx,
            "outcome": w.outcome,
            "read_tokens_est": w.read_tokens_est,
            "read_partial": int(w.read_partial),
            "confidence": w.confidence,
        }
        for w in windows
    ]


def build_rows(parsed: ParsedSession, att: Attribution, components: list[Component],
               sf: SessionFile, repo_root: Path, config: Config,
               ) -> tuple[dict, list[dict], list[dict], list[dict]]:
    static_s = sum(c.static_tokens for c in components if not c.missing)
    cost = compute_session_cost(parsed.events, static_s, config)
    # no parseable event timestamp → fall back to file mtime, otherwise the NULL
    # started_at row is invisible to every windowed query (started_at >= ?)
    fallback_ts = datetime.fromtimestamp(sf.mtime, tz=timezone.utc)
    session_row = {
        "id": parsed.session_id,
        "project": repo_root.name,
        "path": str(sf.path),
        "started_at": (att.started_at or fallback_ts).isoformat(),
        "ended_at": (att.ended_at or fallback_ts).isoformat(),
        "turns": att.turns,
        "cost_usd": cost.cost_usd,
        "prompt_digest": att.prompt_digest,
        "cc_version": parsed.cc_version,
        "parse_status": parsed.parse_status,
        "file_mtime": repr(sf.mtime),
        "file_size": sf.size,
        "parsed_at": now_iso(),
    }
    version_ok = is_supported(parsed.cc_version)
    usage_rows: list[dict] = []
    for (cid, state), agg in sorted(att.usages.items()):
        usage_rows.append(
            {
                "session_id": parsed.session_id,
                "component_id": cid,
                "state": state,
                "count": agg.count,
                # version not in the supported list → all attributions marked heuristic (A11)
                "confidence": agg.confidence if version_ok else "heuristic",
                "evidence": dumps_evidence(agg.evidence),
            }
        )
    if att.git_commit_count:
        usage_rows.append(
            {
                "session_id": parsed.session_id,
                "component_id": GIT_COMMIT_CID,
                "state": "invoked",
                "count": att.git_commit_count,
                "confidence": "heuristic",
                "evidence": dumps_evidence(att.git_commit_evidence),
            }
        )
    usage_rows.append(
        {
            "session_id": parsed.session_id,
            "component_id": COST_CID,
            "state": "invoked",
            "count": 1,
            "confidence": "heuristic",
            "evidence": json.dumps(
                {
                    "cost_usd": cost.cost_usd,
                    "static_cost_usd": cost.static_cost_usd,
                    "static_tokens_s": cost.static_tokens_s,
                    "input_like_tokens": cost.input_like_tokens,
                    "output_tokens": cost.output_tokens,
                    "has_usage": cost.has_usage,
                    "unknown_models": cost.unknown_models,
                    "total_tokens": att.total_tokens,
                    "compactions": att.compaction_count,
                },
                ensure_ascii=False,
            ),
        }
    )
    # self-derivation facts: extracted at ingest because
    # transcripts expire; the marker row makes "extracted, zero facts" distinguishable
    # from "never extracted" (facts coverage in analyze)
    ext = extract_facts(parsed, repo_root)
    fact_rows = build_fact_rows(parsed.session_id, ext)
    usage_rows.append(
        {
            "session_id": parsed.session_id,
            "component_id": FACTS_CID,
            "state": "invoked",
            "count": len(fact_rows),
            "confidence": "heuristic",
            "evidence": json.dumps(
                {"facts": len(fact_rows),
                 "tool_output_tokens_est": ext.tool_output_tokens_est,
                 "extractor": FACTS_EXTRACTOR_VERSION},
                ensure_ascii=False,
            ),
        }
    )
    # stale gauge: extracted at ingest for the same reason as facts (transcripts
    # expire); the marker makes "extracted, zero windows" distinguishable from
    # "never extracted"
    stale_rows = build_stale_rows(parsed.session_id, extract_stale(parsed, repo_root))
    usage_rows.append(
        {
            "session_id": parsed.session_id,
            "component_id": STALE_CID,
            "state": "invoked",
            "count": len(stale_rows),
            "confidence": "heuristic",
            "evidence": json.dumps(
                {"stale_windows": len(stale_rows),
                 "extractor": STALE_EXTRACTOR_VERSION},
                ensure_ascii=False,
            ),
        }
    )
    return session_row, usage_rows, fact_rows, stale_rows


def scan_repo(repo_root: Path, config: Config, since: str | None = None,
              force: bool = False, store: Store | None = None,
              projects_dir: Path | None = None) -> ScanSummary:
    components = load_manifest(repo_root)
    own = store is None
    store = store or open_store(repo_root)
    summary = ScanSummary()
    since_dt = parse_since(since)
    try:
        sessions = discover_sessions(repo_root, projects_dir)
        if not sessions:
            raise PreconditionError(
                "No transcript found for this repo (~/.claude/projects/**/*.jsonl); "
                "check the Claude Code project path"
            )
        for sf in sessions:
            if since_dt and datetime.fromtimestamp(sf.mtime, tz=timezone.utc) < since_dt:
                summary.skipped += 1
                continue
            if not force and not store.needs_update(
                    sf.session_id, sf.mtime, sf.size, FACTS_EXTRACTOR_VERSION,
                    STALE_EXTRACTOR_VERSION):
                summary.skipped += 1
                continue
            existed = store.has_session(sf.session_id)
            try:
                parsed = parse_file(sf.path, sf.sidechain_paths)
                if parsed.cc_version and not is_supported(parsed.cc_version):
                    print(
                        f"warning: session {sf.session_id[:8]} version {parsed.cc_version} "
                        "not in the support matrix; best-effort parse, all attributions marked heuristic",
                        file=sys.stderr,
                    )
                att = attribute(parsed, components, repo_root)
                session_row, usage_rows, fact_rows, stale_rows = build_rows(
                    parsed, att, components, sf, repo_root, config)
                store.replace_session(session_row, usage_rows, fact_rows, stale_rows)
            except Exception as e:
                # one bad session degrades, it must not abort the whole scan (AC5);
                # writes are per-session and transactional, so nothing partial lands
                summary.failed += 1
                print(
                    f"error: session {sf.session_id[:8]} failed to ingest "
                    f"({e.__class__.__name__}: {e}); skipped, other sessions unaffected",
                    file=sys.stderr,
                )
                continue
            if parsed.parse_status == "degraded":
                summary.degraded += 1
                counts = ", ".join(f"{k}×{v}" for k, v in parsed.unknown_type_counts.items())
                print(
                    f"degraded: session {sf.session_id[:8]} unknown events [{counts}]"
                    + (f", bad lines {parsed.bad_line_count}" if parsed.bad_line_count else ""),
                    file=sys.stderr,
                )
            if existed:
                summary.updated += 1
            else:
                summary.new += 1
        return summary
    finally:
        if own:
            store.close()
