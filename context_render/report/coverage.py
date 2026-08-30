"""Coverage aggregation + analyze-facts join (guidance-reachability spec §5).

Reads the EXISTING facts table only — no schema change. Attribution of observed search
cost to files rides on chain_read facts: a chain_read's raw is the read path, its key
links to the search facts whose tokens_est we sum. That attribution is heuristic by
construction (chain_read is heuristic) and renderers keep the ~ mark.

Sorting unreachable rows by observed cost is the gauge discipline: a bare coverage list
invites "document everything" (lint thinking); observed pain first is what the user can
actually act on. store=None or an empty window degrades gracefully — coverage still
renders, join columns stay zero, and the renderer notes `no observed searches (run sync)`.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from ..guidance.graph import ReachResult
from ..guidance.refs import FileIndex
from ..guidance.symbols import py_symbols

SCHEMA_VERSION = 1


def aggregate_coverage(reach: ReachResult, index: FileIndex, *,
                       store=None, since_iso: str | None = None,
                       window_label: str = "last 30d",
                       _fact_rows: list[dict] | None = None) -> dict:
    """Renderer-facing aggregate. `_fact_rows` injects rows directly (tests /
    pre-fetched data); otherwise they are pulled from `store` when given."""
    fact_rows = _fact_rows
    if fact_rows is None and store is not None:
        sessions = store.sessions_since(since_iso)
        fact_rows = [dict(r) for r in store.facts_for_sessions([r["id"] for r in sessions])]
    joined = bool(fact_rows)

    py_files = sorted(f for f in index.files if f.endswith(".py"))
    sym_counts: dict[str, int | None] = {
        f: (None if (s := py_symbols(index.root / f)) is None else len(s)) for f in py_files}
    parse_failed = sum(1 for v in sym_counts.values() if v is None)
    reachable_files = {f for f in index.files if f in reach.reachable}
    reachable_py = [f for f in py_files if f in reachable_files]

    per_file = join_facts(fact_rows or [], index.files)

    def row(f: str) -> dict:
        j = per_file.get(f, {"grep_count": 0, "tokens_est": 0})
        return {"path": f, "defs": sym_counts.get(f), **j}

    unreachable = sorted(
        (row(f) for f in py_files if f not in reachable_files),
        key=lambda r: (-r["tokens_est"], -r["grep_count"], -(r["defs"] or 0), r["path"]))
    grepped_but_reachable = sorted(
        ({"path": f, "hop": reach.hops[f], **per_file[f]}
         for f in reachable_files if f in per_file),
        key=lambda r: (-r["tokens_est"], r["path"]))

    return {
        "schema_version": SCHEMA_VERSION,
        "report_type": "coverage",
        "root_present": "CLAUDE.md" in index.files,
        "window_label": window_label,
        "files": {"total": len(index.files), "reachable": len(reachable_files)},
        "py": {"total": len(py_files), "reachable": len(reachable_py)},
        "symbols": {
            "total": sum(v for v in sym_counts.values() if v is not None),
            "reachable": sum(v for f in reachable_py if (v := sym_counts[f]) is not None),
        },
        "parse_failed": parse_failed,
        "hop_dist": dict(sorted(Counter(reach.hops[f] for f in reachable_py).items())),
        "unreachable": unreachable,
        "grepped_but_reachable": grepped_but_reachable,
        "stale": [{"carrier": c, "raw": raw} for c, raw in sorted(
            {(c, s.raw) for c in reach.stale for s in reach.stale[c]})],
        "joined": joined,
    }


def join_facts(fact_rows: list[dict], repo_files: set[str]) -> dict[str, dict]:
    """file (repo-relative) → {grep_count, tokens_est}. chain_read raws are stored
    repo-relative at extraction (facts extractor v3, normalized against the ingest-time
    repo root — repos move, so the current cwd is never consulted) and match the
    FileIndex directly. Unmappable raws — absolute paths from pre-v3 extractions (sync
    re-extracts them while transcripts exist), outside-repo reads, since-deleted files —
    are skipped: heuristic attribution prefers a miss over a wrong hit (AC2a spirit)."""
    search_by_key: dict[str, list[dict]] = defaultdict(list)
    for r in fact_rows:
        if r["kind"] == "search":
            search_by_key[r["key"]].append(r)

    key_files: dict[str, set[str]] = defaultdict(set)  # attribute once per (key, file)
    for r in fact_rows:
        if r["kind"] == "chain_read" and r["raw"] in repo_files:
            key_files[r["key"]].add(r["raw"])

    out: dict[str, dict] = {}
    for key, files in key_files.items():
        searches = search_by_key.get(key, [])
        for rel in files:
            slot = out.setdefault(rel, {"grep_count": 0, "tokens_est": 0})
            slot["grep_count"] += len(searches) or 1
            slot["tokens_est"] += sum(s["tokens_est"] or 0 for s in searches)
    return out
