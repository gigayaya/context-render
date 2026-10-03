"""Attribute observed search cost to repo files via chain_read facts.

Reads the EXISTING facts table only — no schema change. A chain_read's raw is the read
path, its key links to the search facts whose tokens_est we sum. That attribution is
heuristic by construction (chain_read is heuristic) and renderers keep the ~ mark.
"""

from __future__ import annotations

from collections import defaultdict


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
