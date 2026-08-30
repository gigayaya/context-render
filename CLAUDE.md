# CLAUDE.md

## What this is

`context-render` is a local, read-only observability CLI for Claude Code scaffolding: it parses transcripts (`~/.claude/projects/**/*.jsonl`) and reports which scaffolding components (skills, commands, subagents, MCP servers, hooks, CLAUDE.md files) a session actually used, via a three-state model — **R** registered → **L** loaded → **I** invoked. It is a gauge, not a grader: no scores or verdicts. Core flow makes **zero API calls** and sends no telemetry — this is a hard constraint, not a current state.

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest                    # run tests (pythonpath configured in pyproject)
.venv/bin/ruff check context_render tests    # lint (line-length 100, py311)
PYTHONPATH="$PWD" .venv/bin/ctxr …  # run the CLI during development
```

On some macOS setups the editable install's `.pth` file gets a hidden chflag and `site.py` skips it, so `.venv/bin/ctxr` fails with `ModuleNotFoundError`. Always prefix with `PYTHONPATH="$PWD"` — do not try to reinstall or `chflags` your way out.

## Architecture

Data flow: `cli.py` (typer) → `pipeline.py` (scan orchestration) → subpackages:

- `parser/` — transcript discovery, JSONL loading, Claude Code version handling
- `inventory/` — scans the repo's scaffolds into `.context-render/manifest.yaml`
- `attributor/` — maps transcript events to components (R/L/I) via `rules.py` + `bash_heuristics.py`
- `store/` — SQLite persistence (`schema.sql` ships as package data)
- `report/` — terminal + markdown renderers, timeline, context-window map
- `guidance/` — static guidance-graph reachability (`coverage`): format-neutral path-reference extraction + closure; W5 verdicts frozen in SPIKES.md
- `cost.py` — token/cost estimates from `message.usage` (built-in price table, config-overridable)

Every attribution carries a confidence: **exact** (observed marker in the transcript) or **heuristic** (best-effort inference). Keep the distinction — never promote a heuristic to exact without transcript evidence.

Per-directory conventions live in each directory's own `CLAUDE.md` (`context_render/`, its subpackages, `tests/`, `docs/`, `.github/`) — this file stays big-picture only.

## Gotchas

- **`SPIKES.md` verdicts are frozen.** Each spike decision was validated against real transcripts and locked; overturning one is a change-management event, not a refactor. Read it before touching parser/attributor behavior.
- **`db.sqlite` is an archive, not a cache.** Claude Code expires transcripts (~30 days), so rows for expired sessions are the only remaining record. Never "delete and rebuild" it to fix a problem; `clear` is the only sanctioned deletion path.
- Supported transcript version matrix is **`2.1.*`**; other versions get a warning + best-effort parse. New line `type`s not in the known-auxiliary list count as degraded parsing.

## Docs

`docs/` — three-state model, report reading, configuration, limitations (read before recommending deletions), development.
