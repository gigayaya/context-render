# mapdev/

Routing-map development support for `ctxr map` (merged report) and `ctxr map init`. This
is the paper-convention layer that `guidance/` must never contain: the routing-only
reading, thresholds, and skeleton templates live here, while the only measurement
primitives consumed are `../guidance/refs.py` resolution and markdown syntax. Gauge
discipline holds: every output is a measurement; the study-derived reading appears only
as renderer literature notes.

| name | topic | when to load |
|---|---|---|
| `classify.py` | Per-line classification (blank / code / structural / heading / routing / prose); a heading that resolves a reference is routing | Changing what counts as prose or routing |
| `audit.py` | Merged aggregation: `import_closure` (CLAUDE.md files + @import closure, depth ≤ 5) → `build_reach` with imported carriers as hop-0 starts → carriers (prose share, loading kinds, bare/echo), structure (depth, lazy .md, duplicates), dead routes (reach.stale plus an extract_refs pass over carriers the reach never visited, minus every resolved @import), coverage (py/symbols, unreachable sorted by observed cost) | Changing audit metrics or carrier scope |
| `initgen.py` | Deterministic skeleton (flat ≤ `FLAT_THRESHOLD`, grouped tree beyond) + agent fill instructions; output must pass its own audit clean | Changing skeleton shape or the fill contract |
| `facts_join.py` | chain_read → file attribution of observed search cost (once per key-file pair; raws repo-relative since facts extractor v3); heuristic, keeps the ~ mark | Changing how observed cost joins to files |
