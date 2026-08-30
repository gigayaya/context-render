# context_render package

Top-level package — `cli.py` → `pipeline.py` → subpackages (each with its own CLAUDE.md); core flow makes zero API calls and runtime deps are exactly typer + pyyaml.

| name | topic | when to load |
|---|---|---|
| `__init__.py` | Package version (`__version__`) | Bumping the release version |
| `cli.py` | typer entry point, argument parsing/assembly only; exit codes 0/2/3; reports→stdout, diagnostics→stderr | Adding or changing a CLI command/option |
| `pipeline.py` | discover → parse → attribute → store orchestration shared by `sync` and `last`; `--since` parsing | Changing the scan/ingest flow or session filtering |
| `config.py` | `.context-render/config.yaml` loading + built-in model price table (longest-prefix match on `message.model`) | Adding config keys or updating model prices |
| `cost.py` | Cost engine: measured usage first; static apportioning is approximate and must stay marked "approx." | Changing cost/token math |
| `errors.py` | `PreconditionError`, the only custom error type → exit 3 | Adding a new error path |
| `hookinstall.py` | Idempotent SessionEnd hook install into `.claude/settings.json` (`ctxr sync --since 1d`); legacy `context-render …` commands are upgraded in place on install and matched on removal | Changing hook install/remove behavior |
| `textutil.py` | `clean()` strips terminal control sequences from transcript text at capture time | Handling any new transcript-derived display text |
| `parser/` | Transcript discovery + JSONL → Event stream; all format knowledge encapsulated here | Working under it (own CLAUDE.md) |
| `attributor/` | Events → R/L/I attributions with exact/heuristic confidence | Working under it (own CLAUDE.md) |
| `inventory/` | Scaffolding scan → `.context-render/manifest.yaml` | Working under it (own CLAUDE.md) |
| `report/` | Terminal/markdown renderers, timeline, context-window map | Working under it (own CLAUDE.md) |
| `guidance/` | Static guidance-graph reachability (format-neutral reference extraction, closure, symbols) for `coverage` | Working under it (own CLAUDE.md) |
| `store/` | SQLite persistence (archive, not cache) | Working under it (own CLAUDE.md) |
