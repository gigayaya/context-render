# tests/

Pytest suite (`.venv/bin/python -m pytest`; pythonpath set in pyproject) — everything runs offline against `tmp_path` fixtures whose transcript shapes follow real transcript observations, one test file per module/concern.

| name | topic | when to load |
|---|---|---|
| `conftest.py` | Synthetic repo + transcript builders (`fake_repo`, `fake_projects`; line shapes mirror real transcripts, version pinned to a supported 2.1.x); fixtures follow observed transcripts, not the other way around | Adding fixtures or new event shapes |
| `test_parser.py` | discovery / loader / versions behavior | Changing `parser/` |
| `test_attributor.py` | R/L/I attribution rules | Changing `context_render/attributor/rules.py` |
| `test_bash_heuristics.py` | Shell heuristics, including asserted misses — known false negatives are intended outcomes, don't "fix" production code to make them hit | Changing `bash_heuristics.py` |
| `test_stale.py` | Stale window state machine | Changing `context_render/attributor/stale.py` |
| `test_bash_mutations.py` | Stale-gauge bash heuristics, including asserted misses | Changing `bash_mutations.py` |
| `test_facts.py` | Self-derivation extraction; the decontamination cases are real dry-run contamination samples frozen as regressions | Changing `context_render/attributor/facts.py` |
| `test_structure_tokens.py` | Three-layer syntax-token classifier behavior boundaries; the false-kill cases are real dry-run samples frozen as regressions | Changing `context_render/attributor/structure_tokens.py` |
| `test_analyze.py` | self-derivation aggregation, the `report`/session SELF-DERIVATION blocks, facts backfill and coverage, `report --emit-prompt` | Changing `context_render/report/selfderive.py` or the SELF-DERIVATION blocks |
| `test_inventory.py` | Scanner / manifest / `merge_refresh` | Changing `inventory/` |
| `test_store_scan.py` | Store writes + pipeline scan flow | Changing `store/` or `pipeline.py` |
| `test_migration.py` | DB migration chain | Adding a schema migration |
| `test_report.py` | Renderers + terminal/markdown consistency | Changing `context_render/report/` |
| `test_guidance_refs.py` | Reference extraction/resolution; dry-run findings (bare basenames, slash idioms, placeholders, leading-`/`) frozen as regressions | Changing `context_render/guidance/refs.py` |
| `test_guidance_graph.py` | Closure/provenance/toggles; `test_locked_defaults` is the tripwire for the locked toggle defaults | Changing `context_render/guidance/graph.py` |
| `test_guidance_symbols.py` | Symbol enumeration boundaries | Changing `context_render/guidance/symbols.py` |
| `test_mapdev_classify.py` | Per-line classification boundaries (fences, headings-with-refs, table frames) | Changing `context_render/mapdev/classify.py` |
| `test_mapdev_facts_join.py` | Observed-cost join (chain_read → file), multi-session sums, unmappable raws skipped | Changing `context_render/mapdev/facts_join.py` |
| `test_mapdev_audit.py` | Merged aggregation: loading kinds, @import closure as reach starts, prose/label metrics, dead routes (single source + equivalence gate), depth, py/symbol coverage, facts join degradation, real-store join regression | Changing `context_render/mapdev/audit.py` |
| `test_mapdev_initgen.py` | Skeleton shapes, determinism, and the pass-its-own-audit self-consistency gate | Changing `context_render/mapdev/initgen.py` |
| `test_mapdev_render.py` | `map_lines` section order, optional sections, heuristic marks, term/md dispatch | Changing the map renderer |
| `test_mapdev_cli.py` | `ctxr map` / `ctxr map init` CLI flow, `--since`, exit codes, no-overwrite discipline, removed commands exit 2 | Changing the map commands |
| `test_cli.py` | CLI commands and exit codes; hookinstall idempotency + legacy-hook upgrade/removal | Changing `cli.py` or `hookinstall.py` |
| `test_config.py` | Config loading and price table | Changing `config.py` |
| `test_cost.py` | Cost engine | Changing `cost.py` |
| `test_error_matrix.py` | Precondition / exit-code paths | Changing error handling |
| `test_sanitize.py` | Control-sequence cleaning | Changing `textutil.py` |
