# Routing-map development — `ctxr map`

`ctxr map` and `ctxr map init` operationalize a set of map-design guidelines. The
underlying observation, in one line: in continuous sessions over a changing repo, the
guidance file's shape sets the price of re-checking — routing-only maps kept a re-visit
habit alive, architectural prose suppressed it (one preamble drove change-awareness from
7/10 sessions to 0/10), and a map that never enters the context measured equal to no map
at all.

Both commands are purely static — no transcripts, no API calls — and follow the gauge
discipline: measurements plus a literature note, never scores or pass/fail.

## Guideline → tool surface

| Guideline | Where the tool measures it |
|---|---|
| 0. Guarantee the map gets loaded | `map` loading column: `auto-inject` (root CLAUDE.md), `import` (@import closure, depth ≤ 5), `dir-entry` (per-directory CLAUDE.md, loads on entry); referenced-by-plain-path .md files are listed as **no loading guarantee** |
| 1. Flat list, or pure routing tree ≤ 3 levels | `map` hop depth: max hop, files beyond 3 hops; `map init --shape auto` applies the working rule (flat ≤ 300 files, grouped tree beyond) |
| 2. Every line is path + one-line label | `map` line classification; `bare` counts label-less paths (legal — a bare-path variant measured equal to the labeled one), `echo ~` counts labels that only restate the path (heuristic) |
| 3. No architectural prose anywhere | `map` prose share per carrier, with head position singled out (head-only prose measured *worse* than node-mixed prose) |
| 4. Pair the map with a change signal and the detector | The stale gauge (`ctxr sessions <id-prefix>` STALE COPIES) is the detector's offline form; signal auditing is on the roadmap |
| — Map upkeep (implied) | Dead routes in guidance carriers (references that no longer resolve — the map itself going stale; stale references inside plain referenced docs are listed separately, `~`), duplicate routes, resident-token estimate of the always-loaded set |

## The authoring loop

1. `ctxr map init` — writes a deterministic skeleton: every routable file as
   `- \`path\` — TODO: one-line label`, grouped by top-level directory when the repo is
   large. If a root CLAUDE.md exists it is never touched; the skeleton goes to
   `.context-render/map-proposal.md` instead. An existing proposal is refused, never
   overwritten.
2. Hand `.context-render/map-fill-instructions.md` to your agent — the semantic half
   (which rule a file governs, whether it is the single authority) needs code
   understanding, which is exactly what your own agent session is for. The tool stays
   zero-API-call.
3. `ctxr map` — verify: prose share 0, no dead routes in guidance carriers, no label echoes, depth within 3
   hops, every map component carrying a loading guarantee, and no reachable-but-grepped
   candidates piling up.
4. Re-run `ctxr map` whenever the repo moves; dead routes in guidance carriers are the
   map's own staleness.

## Reading the report honestly

- **Code blocks are counted separately, not as prose.** The study's strict rule keeps
  guidance routing-only; real CLAUDE.md files carry command blocks. The separate count
  is a deliberate tool concession — the measurement is shown, the call is yours.
- **`echo ~` is heuristic** (token overlap between label and path); a flagged line is a
  candidate, not a defect.
- **Scope is the repo.** The user-global `~/.claude/CLAUDE.md` is not a start node: its
  references don't resolve against this repo's tree, so including it would only
  manufacture dead routes (see limitations #12).
- **Dead routes come in two sections.** `dead routes (guidance carriers)` lists the
  audited carriers only (every CLAUDE.md plus the `@import` closure) — that is the map's
  own staleness, the part step 3 asks you to bring to zero. `stale references in
  referenced docs ~` lists every other reachable `.md` the report walked (README, docs,
  anything a carrier links to): the superset the old `coverage` report kept, because an
  agent following the map does open those files. It is heuristic — a path-shaped string
  in prose (`*.py` in a sentence about naming) shows up as an artefact — hence the `~`.
- **Dead routes use strict resolution**: a reference into a runtime-artifact
  directory that is absent on a fresh clone (`.context-render/manifest.yaml`) reads as a
  dead route even though a human resolves it once the artifact exists.
- **`@import` extraction is heuristic**: a token after `@` counts only when it is
  path-shaped (separator, `~` anchor, or a known extension), so prose like
  `@pytest.fixture` is ignored. Imports that resolve to nothing in the repo — a
  user-global `@~/.claude/…` file as much as an in-repo `@missing.md` — are listed
  together under `imports that don't resolve in the repo (external or missing) ~`, which
  carries the `~` mark.
- **Imported carriers are reach starts.** Files routed only from an `@import`ed map count
  as reachable at hop 1 (the platform loads the import with root); a resolvable `@import`
  is never a dead route.
- Structure comparisons beyond "flat vs ≤ 3 levels vs prose-mixed" rest on a single
  batch of the study (its randomized arbitration tested the flat map only) — treat the
  depth and prose measurements as the strongly-evidenced core, the rest as direction.
