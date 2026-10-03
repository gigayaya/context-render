# docs/

User-facing docs stating behavioral contracts (confidence marks, known false negatives, version matrix, estimate error bounds) — when code changes any of those, update the matching doc in the same commit.

| name | topic | when to load |
|---|---|---|
| `three-state-model.md` | R/L/I semantics — the conceptual core | Changing state semantics or applicability |
| `reports.md` | How to read the session report, timeline, context-window map, and the map report | Changing report output |
| `configuration.md` | `.context-render/` layout, config keys, DB backup guidance | Changing config or directory layout |
| `limitations.md` | Numbered list of known limits | Changing attributor/parser behavior (update the matching numbered item); before recommending any deletion |
| `development.md` | Dev setup commands | Changing the build/test workflow |
| `map-authoring.md` | `ctxr map` / `map init`: study-guideline mapping, the authoring loop, honest-reading notes | Changing `mapdev/` behavior or the map commands |
| `images/` | Screenshots used by the docs | Updating visuals after output changes |
