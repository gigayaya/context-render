"""Map aggregation for `ctxr map` (the merged routing-map report).

Audited carriers = every CLAUDE.md in the repo (auto-inject for the root, dir-entry for
the rest — Claude Code platform semantics) plus their @import closure (depth ≤ 5, the
platform's own limit). Referenced-but-not-imported .md files are *listed* as lazy loads,
never audited for prose: docs are where prose belongs, the audit's subject is guidance.

Gauge discipline: every value here is a measurement (line counts, shares, token
estimates, hop depths). The paper-derived reading (why prose share or >3 hops matter)
lives in the renderer's literature notes, not in this module — no verdicts, no scores.
"""

from __future__ import annotations

import posixpath
import re
from collections import Counter, deque

from ..guidance.graph import build_reach
from ..guidance.refs import PATHISH_EXT, FileIndex, extract_refs
from ..guidance.symbols import py_symbols
from ..inventory.tokens import estimate_tokens
from .classify import LineInfo, classify_lines
from .facts_join import join_facts

MAP_SCHEMA_VERSION = 1
IMPORT_DEPTH_MAX = 5  # Claude Code recursive-import limit

_IMPORT_RE = re.compile(r"(?<!\S)@([A-Za-z0-9_~][A-Za-z0-9_./-]*)")
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def import_closure(index: FileIndex) -> tuple[dict[str, str], list[str], list[str],
                                              dict[str, str], dict[str, list[LineInfo]],
                                              set[str]]:
    """Carriers = every CLAUDE.md (auto-inject for root, dir-entry otherwise) plus their
    @import closure (depth ≤ IMPORT_DEPTH_MAX). Returns (audited: path → loading kind in
    discovery order, imports_external, imports_beyond_depth, texts, lines_by,
    import_targets).

    `import_targets` is EVERY resolved @import target seen — including ones already
    audited under another loading kind (a per-directory CLAUDE.md, an import cycle back
    to root) and ones past the depth limit. `audited[p] == "import"` covers only targets
    the closure newly discovered, so it is the wrong set to test an @raw against."""
    claude_mds = [f for f in index.files if posixpath.basename(f) == "CLAUDE.md"]
    claude_mds.sort(key=lambda f: (f != "CLAUDE.md", f))  # root first

    audited: dict[str, str] = {}
    lines_by: dict[str, list[LineInfo]] = {}
    texts: dict[str, str] = {}
    imports_external: list[str] = []
    imports_beyond_depth: list[str] = []
    import_targets: set[str] = set()
    queue: deque[tuple[str, int]] = deque()
    for f in claude_mds:
        audited[f] = "auto-inject" if f == "CLAUDE.md" else "dir-entry"
        queue.append((f, 0))

    while queue:
        path, depth = queue.popleft()
        text = (index.root / path).read_text(encoding="utf-8", errors="replace")
        texts[path] = text
        lines_by[path] = classify_lines(text, path, index)
        for target in _imports(lines_by[path], path, index, imports_external):
            import_targets.add(target)
            if target in audited:
                continue
            if depth >= IMPORT_DEPTH_MAX:
                # the platform won't load it either — but a gauge must say so
                imports_beyond_depth.append(target)
                continue
            audited[target] = "import"
            queue.append((target, depth + 1))
    return audited, imports_external, imports_beyond_depth, texts, lines_by, import_targets


def aggregate_map(index: FileIndex, *, store=None, since_iso: str | None = None,
                  window_label: str = "last 30d",
                  _fact_rows: list[dict] | None = None) -> dict:
    """Renderer-facing aggregate for `ctxr map`: carriers, structure, dead routes, and
    coverage-with-observed-cost, from ONE reach whose starts are root CLAUDE.md plus the
    @import closure (imported carriers load together with root, so they are hop 0).
    `_fact_rows` injects rows directly (tests / pre-fetched data); otherwise they are
    pulled from `store` when given. store=None degrades gracefully: cost columns stay 0."""
    (audited, imports_external, imports_beyond_depth, texts, lines_by,
     import_targets) = import_closure(index)
    imported = [p for p, kind in audited.items() if kind == "import"]
    reach = build_reach(index.root, index, starts=["CLAUDE.md", *imported])

    carriers = [_carrier_row(p, audited[p], texts[p], lines_by[p], index) for p in audited]

    dup_counter: Counter[tuple[str, str]] = Counter()
    lazy_md: set[str] = set()
    for path, lines in lines_by.items():
        for li in lines:
            if li.kind != "routing":
                continue
            for t in li.refs:
                if t != path:
                    dup_counter[(path, t)] += 1
                if (t.endswith(".md") and posixpath.basename(t) != "CLAUDE.md"
                        and t not in audited):
                    lazy_md.add(t)

    # dead routes: reach.stale, plus a per-carrier pass over audited carriers the reach
    # never visited (a CLAUDE.md in a subtree nothing routes to is still a carrier whose
    # staleness is in scope), minus every @import raw — a resolved import ("@docs/a.md", or a
    # relative form like "@a2.md" that normalizes to one) and an external one (already
    # reported via imports_external) are imports, not routes, either way
    stale_pairs = {(c, s.raw) for c, refs in reach.stale.items() for s in refs}
    for p in audited:
        if p not in reach.hops:
            stale_pairs.update((p, s.raw) for s in extract_refs(texts[p], p, index)[1])
    external_raws = set(imports_external)
    dead = sorted({(c, raw) for c, raw in stale_pairs
                   if raw not in external_raws and not _is_import_of(raw, c, import_targets)})

    file_hops = {f: h for f, h in reach.hops.items() if f in index.files}
    reachable_files = set(file_hops)

    fact_rows = _fact_rows
    if fact_rows is None and store is not None:
        sessions = store.sessions_since(since_iso)
        fact_rows = [dict(r) for r in store.facts_for_sessions([r["id"] for r in sessions])]
    per_file = join_facts(fact_rows or [], index.files)

    py_files = sorted(f for f in index.files if f.endswith(".py"))
    sym_counts: dict[str, int | None] = {
        f: (None if (s := py_symbols(index.root / f)) is None else len(s)) for f in py_files}
    reachable_py = [f for f in py_files if f in reachable_files]

    def cost(f: str) -> dict:
        return per_file.get(f, {"grep_count": 0, "tokens_est": 0})

    unreachable_py = sorted(
        ({"path": f, "defs": sym_counts[f], **cost(f)} for f in py_files
         if f not in reachable_files),
        key=lambda r: (-r["tokens_est"], -r["grep_count"], -(r["defs"] or 0), r["path"]))
    grepped_but_reachable = sorted(
        ({"path": f, "hop": file_hops[f], **per_file[f]}
         for f in reachable_files if f in per_file),
        key=lambda r: (-r["tokens_est"], r["path"]))

    return {
        "schema_version": MAP_SCHEMA_VERSION,
        "report_type": "map",
        "root_present": "CLAUDE.md" in index.files,
        "window_label": window_label,
        "joined": bool(fact_rows),
        "carriers": carriers,
        "resident_tokens": sum(c["tokens_est"] for c in carriers
                               if c["loading"] in ("auto-inject", "import")),
        "lazy_md_refs": sorted(lazy_md),
        "imports_external": list(dict.fromkeys(imports_external)),
        "imports_beyond_depth": sorted(set(imports_beyond_depth)),
        "duplicates": [{"carrier": c, "target": t, "count": n}
                       for (c, t), n in sorted(dup_counter.items()) if n > 1],
        "depth": {
            "max_hop": max(file_hops.values(), default=None),
            "over3": sorted(f for f, h in file_hops.items() if h > 3),
            "hop_dist": dict(sorted(Counter(file_hops.values()).items())),
        },
        "dead_routes": [{"carrier": c, "raw": raw} for c, raw in dead],
        "files": {"total": len(index.files), "reachable": len(reachable_files)},
        "py": {"total": len(py_files), "reachable": len(reachable_py)},
        "symbols": {
            "total": sum(v for v in sym_counts.values() if v is not None),
            "reachable": sum(v for f in reachable_py if (v := sym_counts[f]) is not None),
        },
        "parse_failed": sum(1 for v in sym_counts.values() if v is None),
        "unreachable_py": unreachable_py,
        "grepped_but_reachable": grepped_but_reachable,
    }


def _is_import_of(raw: str, carrier: str, import_targets: set[str]) -> bool:
    """True when `raw` is an @import token whose carrier-relative resolution is one of the
    resolved import targets (e.g. `@a2.md` written inside docs/a1.md → docs/a2.md).
    The two-base walk mirrors `_imports` below — same resolution, run backwards over a
    raw the reach reported stale, so the two must stay in step."""
    if not raw.startswith("@"):
        return False
    base = posixpath.dirname(carrier) or "."
    rel = raw[1:]
    for b in (base, "."):
        norm = posixpath.normpath(posixpath.join(b, rel) if b != "." else rel)
        if norm in import_targets:
            return True
    return False


def _imports(lines: list[LineInfo], carrier: str, index: FileIndex,
             external: list[str]):
    """Resolved @import targets in non-code lines; unresolvable ones go to `external`."""
    base = posixpath.dirname(carrier) or "."
    for li in lines:
        if li.kind == "code":
            continue
        for m in _IMPORT_RE.finditer(li.text):
            raw = m.group(1)
            # a target is path-shaped: has a separator, is home-anchored, or carries a
            # known extension — prose tokens like "@import", "@pytest.fixture", "@v1.2"
            # never qualify (heuristic, same notion as refs.py PATHISH_EXT)
            if not ("/" in raw or raw.startswith("~") or raw.endswith(PATHISH_EXT)):
                continue
            target = None
            if not raw.startswith("~"):
                for b in (base, "."):
                    norm = posixpath.normpath(posixpath.join(b, raw) if b != "." else raw)
                    if not norm.startswith("..") and norm in index.files:
                        target = norm
                        break
            if target is None:
                external.append("@" + raw)
            else:
                yield target


def _carrier_row(path: str, loading: str, text: str, lines: list[LineInfo],
                 index: FileIndex) -> dict:
    counts = Counter(li.kind for li in lines)
    content = counts["routing"] + counts["prose"] + counts["heading"]
    prose_lines = [li.number for li in lines if li.kind == "prose"]
    first_routing = next((li.number for li in lines if li.kind == "routing"), None)
    bare = echo = 0
    for li in lines:
        if li.kind != "routing":
            continue
        refs, _ = extract_refs(li.text, path, index)
        remainder = li.text
        path_tokens: set[str] = set()
        for r in refs:
            remainder = remainder.replace(r.raw, " ")
            path_tokens.update(_WORD_RE.findall(r.target.lower()))
        words = set(_WORD_RE.findall(remainder.lower()))
        if not words:
            bare += 1
        elif words <= path_tokens:
            echo += 1
    return {
        "path": path,
        "loading": loading,
        "lines": len(lines),
        "kind_counts": dict(counts),
        "prose_share": (counts["prose"] / content) if content else 0.0,
        "prose_lines": prose_lines,
        # no routing line at all → every prose line precedes the (absent) map,
        # so a pure-prose carrier reads "prose 100% (N in head)" by design
        "head_prose": sum(1 for n in prose_lines
                          if first_routing is None or n < first_routing),
        "bare_paths": bare,
        "label_echoes": echo,
        "tokens_est": estimate_tokens(text),
    }
