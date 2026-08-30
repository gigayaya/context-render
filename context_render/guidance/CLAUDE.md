# guidance/

Format-neutral guidance-graph analysis for `coverage` (and the future acquisition trace) — **format neutrality is a hard requirement**: no knowledge of any project's routing convention may enter this package. The only legal inputs are strings that resolve against the real file tree (reference edges) and Claude Code platform semantics (mechanic edges). Toggle defaults and hygiene rules are frozen W5 verdicts (SPIKES.md #24–#28) — read them before changing any rule.

| name | topic | when to load |
|---|---|---|
| `refs.py` | Path-reference extraction + three-layer resolution (carrier-dir / repo-root exact, unique-basename heuristic with ambiguity abstention); strict stale detection (W5 #28 exclusions); `FileIndex` skip rules | Changing what counts as a reference or as stale |
| `graph.py` | Reachability closure; reference (1 hop) / mechanic (0 hop) / ls (1 hop) edges; priority-queue traversal (minimal hops, `reference` beats `ls` at ties); W5-locked `*_DEFAULT` toggles | Changing traversal, provenance, or edge semantics |
| `symbols.py` | Python def/class enumeration (v1 language scope); parse-failed → `None`, never zero | Changing symbol counting or language scope |
