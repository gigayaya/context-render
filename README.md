# context-render

Which of your Claude Code scaffolds — skills, commands, subagents, MCP servers, hooks, CLAUDE.md files — actually get used?

`context-render` reads your local session transcripts (read-only) and reports each component's state per session: **R** registered → **L** loaded → **I** invoked. No scores, no API calls, no telemetry — everything stays on your machine.

## Install

Requires Python 3.11+. Install from PyPI — `pipx` or `uv tool` keeps the CLI in its own environment:

```bash
pipx install context-render      # or: uv tool install context-render
pip install context-render       # plain pip works too
```

Or install the latest `main` straight from GitHub:

```bash
pip install git+https://github.com/gigayaya/context-render.git
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

![File loads: context injection order with load mechanism and attribution](https://raw.githubusercontent.com/gigayaya/context-render/main/docs/images/file_load.png)

**Timeline** — the session as a chronological event list: hooks firing, CLAUDE.md injection, reads, bash commands, writes. `[L]` marks a load, `[I]` an invocation; `~` marks heuristic (vs. exact) attribution:

![Timeline: chronological session events with L/I state markers](https://raw.githubusercontent.com/gigayaya/context-render/main/docs/images/timeline.png)

**Context-window map** — when tokens entered the window and what put them there: injected loads (▼) above, your actions (▲) below, numbers linking each bar back to its timeline row, and cumulative window occupancy along the bottom:

![Context-window map: injected loads vs. actions over time, with window occupancy](https://raw.githubusercontent.com/gigayaya/context-render/main/docs/images/context_window.png)

The report closes with a **SELF-DERIVATION** block — the top information needs the agent answered itself (searches, repo-structure mapping) with their token and window-occupancy cost; `report` aggregates the same rows across sessions.

See [docs/reports.md](https://github.com/gigayaya/context-render/blob/main/docs/reports.md) for how to read each view in detail.

## Live map in Claude Code (ctxr-live mod)

**ctxr-live** (`claude-mod/`) keeps the current session's context-window map above the prompt while you work — no need to wait for the session to end.

![ctxr-live: the context-window map band drawn above the Claude Code prompt](https://raw.githubusercontent.com/gigayaya/context-render/main/docs/images/ctxr_live.png)

Install inside Claude Code (needs the `ctxr` command, see [Install](#install)):

```
/plugin marketplace add gigayaya/context-render
/plugin install ctxr-live@context-render
```

Type `/ctx` to open the full timeline in a pane. Prefer it beside the transcript? Set the plugin's `layout` option to `sidebar` for a vertical map docked on the right (fullscreen layout, 110+ columns; it falls back to the band otherwise). No `ctxr init` needed; the mod is read-only and makes no API calls. If `ctxr` isn't on your PATH, set the plugin's `command` option in `/config`. Details: [docs/reports.md](https://github.com/gigayaya/context-render/blob/main/docs/reports.md#live-map-above-the-prompt-ctxr-live-mod).

## The routing map

`ctxr map` is the one static view — no transcripts needed. It checks whether your guidance works as a routing map: carrier quality, loading guarantees, dead routes, and which files the agent can reach from root CLAUDE.md versus only by grepping.

`ctxr map init` generates a skeleton (paths + TODO labels) for your agent to fill in. See [docs/map-authoring.md](https://github.com/gigayaya/context-render/blob/main/docs/map-authoring.md).

## The iteration loop

Rewriting a skill's description without observability means never learning whether the next task triggered it. context-render closes that loop:

1. **Write** a skill (or command, subagent, CLAUDE.md).
2. **Run** a real task in Claude Code.
3. **Check** with `ctxr sessions <id-prefix>`: stuck at `R` (never loaded)? The trigger never matched. Stuck at `L` (loaded, never invoked)? The content didn't earn a use. A `STALE COPIES` row never re-read? The file changed and the agent didn't know.
4. **Fix** the trigger or content, run the next task.
5. **Verify** on the next session, or weekly with `report --since 30d`.

It measures "did it fire", not "did it make the output better" — that's an eval question. Before deleting anything, read [docs/limitations.md](https://github.com/gigayaya/context-render/blob/main/docs/limitations.md): used ≠ useful.

## Commands

```
ctxr init        [--refresh] [--yes] [--hook|--no-hook]
ctxr sync        [--since <spec>] [--force]
ctxr sessions    [<id-prefix>] [--since <spec>] [--md] [--evidence] [--full] [--no-timeline] [--no-graph]
ctxr live        <id-prefix> --json [--width 60]   # used by the ctxr-live mod
ctxr report      [--since 30d] [--md] [--no-timeline] [--no-graph] [--emit-prompt <#|key>]
ctxr map         [--md] [--since 30d]
ctxr map init    [--shape auto|flat|tree] [--output <path>]
ctxr clear       [--yes]
ctxr remove-hook
ctxr help | --version
```

| Command | What it does |
|---|---|
| `init` | Scan the repo's scaffolds into `.context-render/manifest.yaml`; optionally install a SessionEnd hook for auto-ingest |
| `sync` | Parse past transcripts into the local db (idempotent; `--force` re-parses everything still on disk) |
| `sessions` | List ingested sessions; `sessions <id-prefix>` shows one session's full report |
| `live` | Read-only JSON snapshot of a (possibly in-progress) session, for the ctxr-live mod |
| `report` | Cross-session aggregate: per-component status (active / low-use / unused / MISS), activity, cost, SELF-DERIVATION. `--emit-prompt` turns one row into a scaffold-drafting prompt |
| `map` | Static routing-map measurements ([docs/map-authoring.md](https://github.com/gigayaya/context-render/blob/main/docs/map-authoring.md)) |
| `map init` | Routing-map skeleton plus fill instructions; never overwrites an existing CLAUDE.md |
| `clear` | Delete the db and reports (manifest/config kept); warns about sessions that can't be rebuilt |
| `remove-hook` | Remove the SessionEnd hook that `init --hook` installed |

Common flags:

- `--since` accepts `30d` / `12w` / `2026-06-01` (bare dates are local midnight; an explicit offset like `2026-06-01T00:00:00+08:00` is honored)
- `--md` writes the complete markdown report to `.context-render/reports/` (terminal output truncates long lists)
- `--full` shows the complete session report in the terminal, keeping colors (no truncation, no file written)
- `--evidence` attaches the raw transcript events behind each attribution to a session report
- `--no-timeline` / `--no-graph` hide report sections; `NO_COLOR` disables colors

## Data & configuration

Everything lives under `<repo>/.context-render/`. `manifest.yaml` is the hand-editable, version-controlled asset. `config.yaml` is optional — thresholds, billing mode, price table: see [docs/configuration.md](https://github.com/gigayaya/context-render/blob/main/docs/configuration.md).

`db.sqlite` is an archive, not a cache: Claude Code expires transcripts after `cleanupPeriodDays` (default 30), after which the db is the only record. It is gitignored — back it up, and never delete and rebuild it to fix a problem.

## Documentation

- [Three-state model & design principles](https://github.com/gigayaya/context-render/blob/main/docs/three-state-model.md) — what R/L/I mean and how to act on them
- [Reading the reports](https://github.com/gigayaya/context-render/blob/main/docs/reports.md) — file loads, timeline, context-window map, live map mod, colors, exit codes
- [Configuration](https://github.com/gigayaya/context-render/blob/main/docs/configuration.md) — directory layout, config.yaml, SessionEnd hook
- [Limitations](https://github.com/gigayaya/context-render/blob/main/docs/limitations.md) — read before deleting anything
- [Development](https://github.com/gigayaya/context-render/blob/main/docs/development.md)

## Privacy

Zero uploads, zero telemetry, zero API calls in the core flow, and the ctxr-live mod adds none either; transcripts are read-only and all outputs live in your repo.
