# context-render

Which of your Claude Code scaffolds — skills, commands, subagents, MCP servers, hooks, CLAUDE.md files — actually get used?

`context-render` reads your local session transcripts (read-only) and reports each component's state per session: **R** registered → **L** loaded → **I** invoked. No scores, no API calls, no telemetry — everything stays on your machine.

## Install

Requires Python 3.11+. Install straight from GitHub:

```bash
pip install git+https://github.com/gigayaya/context-render.git
```

Or clone first:

```bash
git clone https://github.com/gigayaya/context-render.git
cd context-render
pip install .
```

Either way you get the `ctxr` command on your PATH.

## Quickstart

```bash
cd your-repo
ctxr init           # scan scaffolds → .context-render/manifest.yaml
ctxr sync           # ingest past sessions (idempotent, safe to re-run)
ctxr sessions       # list ingested sessions, newest on top
ctxr sessions <id>  # one session: what was loaded/invoked, full timeline
```

Once you have some history:

```bash
ctxr report --since 30d      # cross-session aggregate: active / low-use / unused, plus what the agent had to go find itself
ctxr map                     # static: is your guidance a usable routing map, and what does it fail to cover?
```

## What a session report looks like

Every session report (`sessions <id-prefix>`) has three views of the same session:

**File loads** — every file that entered the context, in injection order, with how it got there (`Read`, `Bash`, system-injected) and, where possible, which component it's attributed to:

![File loads: context injection order with load mechanism and attribution](docs/images/file_load.png)

**Timeline** — the session as a chronological event list: hooks firing, CLAUDE.md injection, reads, bash commands, writes. `[L]` marks a load, `[I]` an invocation; `~` marks heuristic (vs. exact) attribution:

![Timeline: chronological session events with L/I state markers](docs/images/timeline.png)

**Context-window map** — when tokens entered the window and what put them there: injected loads (▼) above, your actions (▲) below, numbers linking each bar back to its timeline row, and cumulative window occupancy along the bottom:

![Context-window map: injected loads vs. actions over time, with window occupancy](docs/images/context_window.png)

The report closes with a **SELF-DERIVATION** block — the top information needs the agent answered itself (searches, repo-structure mapping) with their token and window-occupancy cost; `report` aggregates the same rows across sessions.

See [docs/reports.md](docs/reports.md) for how to read each view in detail.

## The routing map

`ctxr map` is the one static view — no transcripts needed. It measures your guidance *as a routing map* and what the map fails to cover: per-carrier prose share and label quality, loading guarantees (auto-inject / `@import` / dir-entry / plain reference), structure and hop depth, dead routes (references whose targets no longer exist — the map's own staleness), and which files and Python symbols the agent can reach from root CLAUDE.md by following references versus only by grepping. Unreachable files are sorted by the search cost actually observed in your sessions when a db exists.

`ctxr map init` generates a deterministic skeleton (paths + TODO labels) plus fill instructions for your agent, which does the semantic half so the tool stays offline. The guidelines behind the measurements come from the research program this tool grew out of; see [docs/map-authoring.md](docs/map-authoring.md) for the mapping and the authoring loop.

## The iteration loop

Writing scaffolds without observability is shooting without watching the rim: you rewrite a skill's description and never learn whether the next task triggered it. context-render closes that loop:

1. **Write** a skill (or command, subagent, CLAUDE.md).
2. **Run** a real task in Claude Code.
3. **Check**: `ctxr sessions` to find the session, then `ctxr sessions <id-prefix>` — stuck at `R` (never loaded)? The description/trigger never matched. Stuck at `L` (loaded, never invoked)? The content didn't earn a use. A `STALE COPIES` row that never re-read? The world changed and the agent didn't know.
4. **Fix** the trigger or the content, run the next task.
5. **Verify the fix landed**: run `sessions <id-prefix>` on the next session, or `report --since 30d` to see the component's state across recent sessions.

The loop stays inside "did it fire". Whether the scaffold made the output *better* is an eval question, and evals belong to the scaffold's author — this tool's job is to make the firing observable.

- **After a task**: run `sessions`, then `sessions <id-prefix>` on the new row.
- **Weekly**: run `report --since 30d`, review unused components, delete or rewrite low-use ones.

Before deleting anything, read [docs/limitations.md](docs/limitations.md) — used ≠ useful, and low use may just mean no relevant task came up in the window.

## Commands

```
ctxr init        [--refresh] [--yes] [--hook|--no-hook]
ctxr sync        [--since <spec>] [--force]
ctxr sessions    [<id-prefix>] [--since <spec>] [--md] [--evidence] [--full] [--no-timeline] [--no-graph]
ctxr report      [--since 30d] [--md] [--no-timeline] [--no-graph] [--emit-prompt <#|key>]
ctxr map         [--md] [--since 30d]
ctxr map init    [--shape auto|flat|tree] [--output <path>]
ctxr clear       [--yes]
ctxr remove-hook
ctxr help
```

| Command | What it does |
|---|---|
| `init` | Scan the repo's scaffolds into `.context-render/manifest.yaml`; optionally installs a SessionEnd hook for auto-ingest |
| `sync` | Parse past transcripts into the local db (idempotent; `--force` rebuilds) |
| `sessions` | List ingested sessions; `sessions <id-prefix>` shows any one session's full report |
| `report` | Cross-session aggregate over a time window: per-component status (active / low-use / unused / MISS), daily activity, cost estimate, and a SELF-DERIVATION block — every agent search is a question the harness didn't answer: what the agent went after, grouped and sorted by token cost. `--emit-prompt` packs one row's evidence into a scaffold-drafting prompt (plain text, offline) |
| `map` | The routing map, measured: prose share per guidance carrier, loading guarantees, bare/echo labels, structure and hop depth, dead routes, and file/symbol reachability from root CLAUDE.md — unreachable `.py` sorted by observed search cost when a db exists. Static, facts with literature notes, no scores ([docs/map-authoring.md](docs/map-authoring.md)) |
| `map init` | Deterministic routing-map skeleton (paths + TODO labels) plus agent fill instructions; never overwrites — an existing CLAUDE.md sends the skeleton to `.context-render/map-proposal.md` |
| `clear` | Delete recorded data (db + reports); manifest/config are kept. `sync` only rebuilds sessions whose transcripts still exist — `clear` names the ones that would be lost for good |
| `remove-hook` | Remove the SessionEnd hook that `init --hook` installed, including hooks written before the `ctxr` rename (other settings untouched) |

Common flags:

- `--since` accepts `30d` / `12w` / `2026-06-01` (bare dates are local midnight; an explicit offset like `2026-06-01T00:00:00+08:00` is honored)
- `--md` writes the complete markdown report to `.context-render/reports/` (terminal output truncates long lists)
- `--full` shows the complete session report in the terminal, keeping colors (no truncation, no file written)
- `--no-timeline` / `--no-graph` hide report sections; `NO_COLOR` disables colors

Session reports include a per-file load list, an action timeline, and a context-window map — see [docs/reports.md](docs/reports.md) for how to read them.

## Data & configuration

Everything lives under `<repo>/.context-render/`. `manifest.yaml` is the hand-editable, version-controlled asset. `config.yaml` is optional — thresholds, billing mode, price table: see [docs/configuration.md](docs/configuration.md).

`db.sqlite` is an archive, not a cache. Claude Code expires transcripts on a rolling window (`cleanupPeriodDays`, default 30 days), so `~/.claude/projects/` is a buffer rather than a source of truth: a full re-parse takes well under a second, but it can only recover sessions whose transcripts are still on disk. Once a transcript expires, its rows in `db.sqlite` are the only record left. It is gitignored — back it up if you want history beyond the retention window, and never "delete and rebuild" it to fix a problem.

## Documentation

- [Three-state model & design principles](docs/three-state-model.md) — what R/L/I mean and how to act on them
- [Reading the reports](docs/reports.md) — file loads, timeline, context-window map, colors, exit codes
- [Configuration](docs/configuration.md) — directory layout, config.yaml, SessionEnd hook
- [Limitations](docs/limitations.md) — read before deleting anything
- [Development](docs/development.md)

## Privacy

Zero uploads, zero telemetry, zero API calls in the core flow; transcripts are read-only and all outputs live in your repo.
