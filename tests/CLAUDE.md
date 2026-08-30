# tests/

Pytest suite (`.venv/bin/python -m pytest`; pythonpath set in pyproject) — everything runs offline against `tmp_path` fixtures whose transcript shapes follow the frozen SPIKES.md observations, one test file per module/concern.

| name | topic | when to load |
|---|---|---|
| `conftest.py` | Synthetic repo + transcript builders (`fake_repo`, `fake_projects`; line shapes mirror SPIKES.md, version pinned to a supported 2.1.x); fixtures follow SPIKES, not the other way around | Adding fixtures or new event shapes |
| `test_parser.py` | discovery / loader / versions behavior | Changing `parser/` |
| `test_attributor.py` | R/L/I attribution rules | Changing `attributor/rules.py` |
| `test_bash_heuristics.py` | Shell heuristics, including asserted misses — known false negatives are intended outcomes, don't "fix" production code to make them hit | Changing `bash_heuristics.py` |
| `test_stale.py` | Stale window state machine (upgrade-plan §2.7 cases 1-8) | Changing `attributor/stale.py` |
| `test_bash_mutations.py` | Stale-gauge bash heuristics, including asserted misses | Changing `bash_mutations.py` |
| `test_facts.py` | Self-derivation extraction; the decontamination cases are real dry-run contamination samples (W3 #19) frozen as regressions | Changing `attributor/facts.py` |
| `test_structure_tokens.py` | Three-layer syntax-token classifier behavior boundaries; the false-kill cases are real W4 dry-run samples frozen as regressions | Changing `attributor/structure_tokens.py` |
| `test_analyze.py` | analyze aggregation/rendering/CLI, facts backfill and coverage, emit-prompt | Changing `report/selfderive.py` or the analyze command |
| `test_inventory.py` | Scanner / manifest / `merge_refresh` | Changing `inventory/` |
| `test_store_scan.py` | Store writes + pipeline scan flow | Changing `store/` or `pipeline.py` |
| `test_migration.py` | DB migration chain | Adding a schema migration |
| `test_report.py` | Renderers + terminal/markdown consistency | Changing `report/` |
| `test_guidance_refs.py` | Reference extraction/resolution; W5 dry-run findings (bare basenames, slash idioms, placeholders, leading-`/`) frozen as regressions | Changing `guidance/refs.py` |
| `test_guidance_graph.py` | Closure/provenance/toggles; `test_w5_locked_defaults` is the verdict tripwire | Changing `guidance/graph.py` |
| `test_guidance_symbols.py` | Symbol enumeration boundaries | Changing `guidance/symbols.py` |
| `test_coverage_agg.py` | Coverage aggregation, facts join, renderer, CLI | Changing `report/coverage.py` or the coverage command |
| `test_cli.py` | CLI commands and exit codes; hookinstall idempotency + legacy-hook upgrade/removal | Changing `cli.py` or `hookinstall.py` |
| `test_config.py` | Config loading and price table | Changing `config.py` |
| `test_cost.py` | Cost engine | Changing `cost.py` |
| `test_error_matrix.py` | Precondition / exit-code paths | Changing error handling |
| `test_sanitize.py` | Control-sequence cleaning | Changing `textutil.py` |
