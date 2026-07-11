# The three-state model

context-render reports every scaffolding component (skill, command, subagent, MCP server, hook, CLAUDE.md file) in one of three states per session:

| State | Meaning |
|---|---|
| `R` registered | Registered in context (anything in the manifest that isn't missing is R) |
| `L` loaded | Content was loaded/expanded into the context window |
| `I` invoked | Actually invoked/triggered |

## Why the distinction matters

"Loaded but not invoked" and "never loaded" are different diagnoses:

- **Never loaded** (stuck at `R`): the description/trigger is broken — the agent never found the component.
- **Loaded but not invoked** (stuck at `L`): the agent saw the content but didn't adopt it — the content itself didn't earn a use.

This maps directly to "where to make the next cut": a stuck-at-R component needs a better description or trigger; a stuck-at-L component needs its content rewritten (or deleted).

## Design principles

- **A gauge, not a grader**: context-render emits no scores, rankings, or verdicts. It answers *coverage* ("was this used?"), not *effectiveness* ("did this help?"). The code-coverage analogy holds: 100% coverage doesn't mean correct, 0% coverage is worth suspicion.
- **Passive-first**: it reads Claude Code's existing transcripts (`~/.claude/projects/**/*.jsonl`, read-only) — no worktrees, no headless agents.
- **Zero API calls, zero telemetry in the core**: everything stays local; the core flow runs offline.
- Phase one supports Claude Code only; built for scaffold authors' own use.
