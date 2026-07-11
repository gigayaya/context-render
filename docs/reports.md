# Reading the reports

Anatomy of the session report (`last` / `sessions <id-prefix>`) and output conventions shared by all commands.

## File loads (context injection order)

The session report includes a "file loads" section: **every file** the agent read into context during that session (not limited to manifest components), listed in injection order, with per-tool counts (Read exact / Bash file-read heuristic marked `~`). Each file read appears on the timeline as a `[read]` row (manifest component files already have an `[L]` row and aren't duplicated).

Failed reads (tool_result is an error) don't count — what never entered context isn't loaded.

Reads made with the Read tool also show **which lines** entered context, one token per load in read order: `:80–160` (offset+limit), `:159–` (offset only, up to the tool's 2000-line cap), and — only when listed alongside real ranges — `:all` for a read without offset/limit. A file whose reads all lack offset/limit stays unmarked: the tool caps whole-file reads at 2000 lines, so labeling them "all" would overstate what entered context. Bash file reads never carry a range (parsing one out of `sed`/`head` would be heuristic). More than 4 ranges fold into `+n`; `--full`/`--md` list them all.

The terminal truncates at 25 files; `--full` lifts the truncation in the terminal (colors kept), and `--md` is always complete.

## Timeline

The timeline also shows **agent actions**: `[edit]` / `[write]` / `[bash]` (file changes and command execution), interleaved with `[read]`/`[L]`/`[I]` to present "what was loaded → based on what → what was done".

Failed actions (tool_result is an error) are still listed and marked "(failed)" — the action happened, which is semantically different from loading.

Every timeline row is numbered; the context window map labels its marks with these numbers.

Between the timestamp and the event, each row shows the **measured context-window
occupancy** at that point (`21k` style): the prompt-side usage (input + cache read +
cache creation) of the latest main-thread assistant message at or before the row's
event, carried forward. Rows inside the same turn share one value — a tool result
only shows up in the number at the next assistant turn. A `·` means no measurement
applies: rows before the first assistant message, rows after a compaction until the
next sample (the pre-compaction number is known-wrong), and `[subagent:…]` rows,
which live in the subagent's own window. Every number is a transcript measurement —
this column never mixes in estimates.

## Context window map

Before the timeline, the session report has a **context window visualization**: a rectangular bar represents the session's context window (left→right in event order).

- `▼` above marks context-injecting events (file loads, component L).
- `▲` below marks agent actions (edit/write/bash, component I).
- Each mark is labeled with its row number in the timeline listing, so the two views cross-reference (dense areas stack labels into extra lanes; labels that don't fit are dropped).
- Inside the box each event paints a block in its own lane and color — top lane cyan for injections, bottom lane yellow for actions — whose height ≈ the event's estimated context tokens (from tool-result sizes and manifest estimates, √-compressed against the session's largest event so small events stay visible; unknown sizes show the minimum tick).
- `⟐` marks compaction across both lanes.

Below it, a second `window` bar shows **occupancy** (green): prompt-token usage per turn against the model window (denominator `context_window_tokens`, default 200k; a peak above it is proof of a bigger window and snaps the denominator to the next known tier, i.e. 1M — for 1M-window sessions that never cross 200k, set `context_window_tokens: 1000000` in config), linear scale, sharing the event bar's columns — so a compaction dip lines up vertically with its `⟐`. It only appears when the transcript carries usage data; `--no-graph` turns the map off.

## Subagents

Subagent transcripts (stored by Claude Code as separate files under `<session-id>/subagents/`) are merged into the parent session chronologically. What a subagent loaded or invoked counts toward the session's component states, and its evidence and timeline rows are tagged `[subagent:<type>]`; its token usage counts toward the session total and cost.

Each subagent runs in **its own context window**, so the window-shaped views stay main-chain only: sidechain reads don't enter file loads (context injection order), and the context window map and occupancy bar don't draw sidechain events. Session stats (turns, task digest) also stay main-chain — a subagent's "user" message is the dispatch prompt the main agent wrote, not a user turn.

## Common flags and conventions

- `--since` accepts `30d` / `12w` / `2026-06-01` (by started_at, local timezone).
- `--md`: the full markdown version is written to `.context-render/reports/` and also printed to stdout. `--md` output is always plain text, matching the terminal line for line.
- `--full` (session reports): print the complete report to the terminal — untruncated file loads and timeline, plus the `--evidence` detail when requested — while keeping ANSI colors. Nothing is written to disk.
- `sessions` lists ingested sessions; `sessions <id-prefix>` inspects the full report of any one of them (not limited to the latest; the "in-progress exclusion" isn't applied when an id is specified).

## Terminal colors

ANSI colors are enabled automatically when stdout is a tty (invoked green, loaded cyan, deadweight/MISS red, warnings yellow, unused rows dimmed); set `NO_COLOR` to disable, `CLICOLOR_FORCE=1` to force on (pairs with `less -R`).

On the timeline the action tags are also colored: `[read]` blue, `[edit]`/`[write]` yellow, `[bash]` and compaction magenta, component `[L]` cyan, `[I]` green, and any `(failed)` marker red.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `2` | CLI argument error |
| `3` | Precondition/environment error (not initialized, transcripts not found, DB schema mismatch, invalid config) |

`1` is reserved and unused.
