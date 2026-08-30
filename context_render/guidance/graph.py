"""Reachability closure over guidance (guidance-reachability spec §2).

Two edge kinds, both format-neutral:
- reference: a resolvable path string in a carrier's text (refs.py), costs 1 hop
- mechanic: Claude Code platform semantics — a reachable node loads every CLAUDE.md on
  its directory chain — costs 0 hops (hardcoded, not configurable: platform, not convention)
- ls: with dir_children on, a referenced dir exposes its direct child files (1 hop)

Toggle defaults below are pre-W5 leanings (spec §7) and get LOCKED at the Task 5 gate;
the interface never changes with the verdict (W4 precedent).

Traversal is a priority queue on (hop, edge-kind) so hops stay minimal AND provenance is
deterministic at ties: an explicitly documented path (reference) beats ls-discoverability
— the gauge should report the strongest evidence for how a node is findable.
"""

from __future__ import annotations

import heapq
import posixpath
from dataclasses import dataclass, field
from pathlib import Path

from .refs import FileIndex, StaleRef, extract_refs

# W5 verdicts (2026-07-19, n=5 repos, frozen — see SPIKES.md #24–#26):
FENCED_DEFAULT = True        # #24: fenced refs ARE routing (tree-diagram convention);
#                              resolution itself filters the pollution (hypothetical
#                              example names never resolve → stale channel, not edges)
ALL_MD_DEFAULT = True        # #25: decisive — guidelines/knowledge-base style projects
#                              go 0/21 → 16/21 py on this toggle alone
DIR_CHILDREN_DEFAULT = True  # #26: real one-ls-away coverage; `ls` provenance stays
#                              distinct from `reference`


@dataclass(frozen=True)
class Provenance:
    carrier: str   # carrier file for reference edges; triggering node for mechanic/ls
    raw: str
    kind: str      # "reference" | "mechanic" | "ls" | "start"


@dataclass
class ReachResult:
    reachable: set[str] = field(default_factory=set)
    hops: dict[str, int] = field(default_factory=dict)
    via: dict[str, Provenance] = field(default_factory=dict)
    stale: dict[str, list[StaleRef]] = field(default_factory=dict)


def build_reach(root: Path, index: FileIndex, *,
                starts: list[str],
                fenced: bool = FENCED_DEFAULT,
                all_md: bool = ALL_MD_DEFAULT,
                dir_children: bool = DIR_CHILDREN_DEFAULT,
                external_carriers: dict[str, str] | None = None) -> ReachResult:
    """Closure from `starts` (repo-relative guidance files; missing ones are silently
    skipped — near-zero output is a signal, not an error). `external_carriers` maps a
    display name (e.g. "<global>") to guidance text resolved root-relative, hop 0."""
    res = ReachResult()
    pri = {"start": 0, "mechanic": 0, "reference": 1, "ls": 2}
    heap: list[tuple[int, int, int, str, Provenance]] = []
    seq = 0

    def push(node: str, hop: int, prov: Provenance):
        nonlocal seq
        if node not in res.reachable:
            heapq.heappush(heap, (hop, pri[prov.kind], seq, node, prov))
            seq += 1

    for s in starts:
        if s in index.files:
            push(s, 0, Provenance("<start>", s, "start"))
    for name, text in (external_carriers or {}).items():
        refs, stale = extract_refs(text, "CLAUDE.md", index)
        if stale:
            res.stale[name] = stale
        for ref in refs:
            if ref.context == "fenced" and not fenced:
                continue
            push(ref.target, 1, Provenance(name, ref.raw, "reference"))

    while heap:
        hop, _, _, node, prov = heapq.heappop(heap)
        if node in res.reachable:
            continue
        res.reachable.add(node)
        res.hops[node] = hop
        res.via[node] = prov

        for cm in _chain_claude_mds(node, index):  # mechanic: costs 0 hops
            push(cm, hop, Provenance(node, cm, "mechanic"))

        if node in index.dirs and dir_children:
            prefix = node + "/"
            for f in sorted(index.files):
                if f.startswith(prefix) and "/" not in f[len(prefix):]:
                    push(f, hop + 1, Provenance(node, f, "ls"))

        if node in index.files and _is_carrier(node, all_md):
            refs, stale = extract_refs((index.root / node).read_text(
                encoding="utf-8", errors="replace"), node, index)
            if stale:
                res.stale[node] = stale
            for ref in refs:
                if ref.context == "fenced" and not fenced:
                    continue
                push(ref.target, hop + 1, Provenance(node, ref.raw, "reference"))
    return res


def _is_carrier(node: str, all_md: bool) -> bool:
    if posixpath.basename(node) == "CLAUDE.md":
        return True
    return all_md and node.endswith(".md")


def _chain_claude_mds(node: str, index: FileIndex) -> list[str]:
    """Every CLAUDE.md on the node's directory chain (the dir itself for dirs)."""
    out = []
    d = node if node in index.dirs else posixpath.dirname(node)
    while True:
        cm = posixpath.join(d, "CLAUDE.md") if d else "CLAUDE.md"
        if cm in index.files:
            out.append(cm)
        if not d:
            return out
        d = posixpath.dirname(d)
